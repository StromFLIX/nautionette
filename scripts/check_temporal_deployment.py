"""Exercise the actual Coolify hook/controller on a disposable Docker stack.

The fake app services only provide idle volumes and an authenticated-health
fixture. PostgreSQL, legacy/new Temporal, backup/restore, SDK probes, hook code,
controller crash fencing and broker pause/unpause use real Docker operations.
No live deployment API or infrastructure credentials are needed.
"""

from __future__ import annotations

import asyncio
import os
import subprocess

import docker
from temporalio.client import Client

from scripts.check_temporal_upgrade import QUEUE, ROOT, Rehearsal, UpgradeProbe, checkpoint, resume
from scripts.temporal_deploy import candidate_files
from scripts.temporal_deploy_hook import hook, state

# ruff: noqa: S603, S607 - fixed CI fixture commands, never caller input


async def run():
    fixture = Rehearsal()
    client = docker.from_env()
    internal = fixture.prefix + "-internal"
    controller = "nautionette-temporal-" + fixture.prefix
    services = {}
    try:
        fixture.docker("network", "create", "--internal", fixture.network)
        client.networks.create(internal)
        pg = fixture.postgres("postgres")
        # Coolify adds a shared network as well as Compose's private data net.
        client.networks.get(internal).connect(pg, aliases=["postgres-coolify"])
        # Match the real preflight discovery: Coolify had persisted a 1.29.0 build arg.
        legacy = fixture.build("1.29.0", "legacy")
        old, address = fixture.start(legacy, public_network=internal)
        fixture.wait(
            lambda: fixture.docker(
                "exec", old, "temporal", "operator", "namespace", "describe", "--namespace", "default"
            )
        )
        await asyncio.sleep(10)
        sdk = await Client.connect(address)
        await sdk.start_workflow(
            UpgradeProbe.run, "application-pending", id="application-pending", task_queue=QUEUE
        )
        await checkpoint(sdk, ["application-pending"])

        image = fixture.prefix + "/sdk:fixture"
        subprocess.run(
            ["docker", "build", "-t", image, "-f", str(ROOT / "services/worker/Dockerfile"), str(ROOT)],
            check=True,
        )
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=ROOT).strip()
        volumes = {
            name: fixture.prefix + "-" + name
            for name in (
                "backend-data",
                "workflows",
                "artifacts",
                "projects",
                "chat-sessions",
                "maxio-data",
                "agentgateway-data",
            )
        }
        fixture.volumes.extend(volumes.values())
        mappings = {
            "backend": {
                "/data": "backend-data",
                "/workflows": "workflows",
                "/artifacts": "artifacts",
                "/projects": "projects",
                "/chat-sessions": "chat-sessions",
                "/output-storage": "maxio-data",
            },
            "worker": {"/workflows": "workflows", "/artifacts": "artifacts"},
            "docker-broker": {"/projects": "projects", "/chat-sessions": "chat-sessions"},
            "maxio": {"/data": "maxio-data"},
            "agentgateway": {"/data": "agentgateway-data"},
        }
        http = """import json,signal,sys
from http.server import BaseHTTPRequestHandler,HTTPServer
signal.signal(signal.SIGTERM,lambda *_:sys.exit(0))
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  names=('temporal','broker','agentgateway','workflow-mcp')
  value={'environment':'staging','auth_enabled':True,
         'components':[{'name':n,'status':'ok'} for n in names]}
  if self.path.endswith('/storage'): value={'health':{'status':'ok'}}
  self.send_response(200);self.end_headers();self.wfile.write(json.dumps(value).encode())
HTTPServer(('0.0.0.0',8080),Handler).serve_forever()
"""
        idle = "import time,signal,sys;signal.signal(signal.SIGTERM,lambda *_:sys.exit(0));time.sleep(36000)"
        mcp_server = """import socket,threading,signal,sys
workers=[]
def connection(c):
 with c:
  c.sendall(b'r')
  while c.recv(1024): pass
def stop(*_):
 for worker in workers: worker.join()
 sys.exit(0)
signal.signal(signal.SIGTERM,stop)
s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
s.bind(('0.0.0.0',8901));s.listen()
while True:
 c,_=s.accept();t=threading.Thread(target=connection,args=(c,));t.start();workers.append(t)
"""
        gateway = f"""import socket,signal,sys,time,pathlib
for attempt in range(60):
 try:
  c=socket.create_connection(({fixture.prefix + "-workflow-mcp"!r},8901));break
 except OSError: time.sleep(0.5)
else: sys.exit(1)
assert c.recv(1)==b'r'
signal.signal(signal.SIGTERM,lambda *_:(c.close(),sys.exit(0)))
pathlib.Path('/tmp/stream-connected').touch()
time.sleep(36000)
"""
        maxio = (
            "import time,signal,sys;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
            "signal.signal(signal.SIGINT,lambda *_:sys.exit(0));time.sleep(36000)"
        )
        for service in (
            "backend",
            "docker-broker",
            "worker",
            "workflow-mcp",
            "agentgateway",
            "frontend-web",
            "website",
            "maxio",
        ):
            name = fixture.prefix + "-" + service
            container = client.containers.run(
                image,
                [
                    "python",
                    "-c",
                    {
                        "backend": http,
                        "workflow-mcp": mcp_server,
                        "agentgateway": gateway,
                        "maxio": maxio,
                    }.get(service, idle),
                ],
                name=name,
                detach=True,
                user="0:0",
                network=internal,
                labels={"com.docker.compose.project": fixture.prefix, "com.docker.compose.service": service},
                volumes={
                    volumes[dataset]: {
                        "bind": destination,
                        "mode": "ro" if destination == "/output-storage" else "rw",
                    }
                    for destination, dataset in mappings.get(service, {}).items()
                },
                environment={
                    "APP_ENVIRONMENT": "staging",
                    "APP_TOKEN": "fixture-only",
                    "APP_VERSION": commit,
                    "TARGET_NETWORK": internal,
                    "WORKFLOWS_VOLUME": volumes["workflows"],
                },
                restart_policy={"Name": "unless-stopped"},
            )
            fixture.containers.append(name)
            services[service] = container
        fixture.wait(
            lambda: fixture.docker(
                "exec",
                services["agentgateway"].id,
                "python",
                "-c",
                "from pathlib import Path;assert Path('/tmp/stream-connected').exists()",
            )
        )
        own = os.environ.get("HOSTNAME")
        os.environ["HOSTNAME"] = services["docker-broker"].id
        payload = {
            "app": fixture.prefix,
            "commit": commit,
            "target": "staging",
            "mode": "prepare",
            "shards": "4",
            "files": candidate_files(commit),
        }
        print(
            "Exercising real pre-deployment hook, restored snapshot and consecutive server minors", flush=True
        )
        hook(payload)
        job = client.containers.get(controller)
        assert state(job)["status"] == "prepared"
        # The controller must let the hook's broker return, without restarting old Temporal.
        async with asyncio.timeout(30):
            while True:
                services["docker-broker"].reload()
                if not services["docker-broker"].attrs["State"].get("Paused"):
                    break
                await asyncio.sleep(0.5)
        old_container = client.containers.get(old)
        assert old_container.status == "exited"
        run_id = next(
            item.split("=", 1)[1]
            for item in job.attrs["Config"]["Env"]
            if item.startswith("MAINTENANCE_RUN_ID=")
        )
        final_image = "nautionette/temporal-maintenance:" + run_id + "-server-1.32.2"
        old_container.remove()
        new, address = fixture.start(final_image, fence=(fixture.prefix, commit), public_network=internal)
        for container in services.values():
            container.start()
        hook({**payload, "mode": "finish"})
        assert state(job)["status"] == "completed"
        job.reload()
        assert job.status == "exited"
        assert job.attrs["HostConfig"]["RestartPolicy"]["Name"] == "no"
        # Completion is readable without executing or restarting the controller.
        hook({**payload, "mode": "finish"})
        assert state(job)["status"] == "completed"
        sdk = await Client.connect(address)
        await resume(sdk, ["application-pending"])
        # Coolify may invoke a hook twice; no second snapshot or schema mutation.
        hook(payload)
        assert client.containers.get(controller).id == job.id
        assert state(job)["status"] == "completed"
        if own is None:
            os.environ.pop("HOSTNAME", None)
        else:
            os.environ["HOSTNAME"] = own
        print("PASS: native hooks, broker pause, restore, minor hops, resumption, duplicate delivery")
    except Exception:
        try:
            job = client.containers.get(controller)
            if job.status != "running":
                print("Stopped controller state:", state(job))
                raise
            result = job.exec_run(
                [
                    "python",
                    "-c",
                    "import os,pathlib;root=pathlib.Path('/var/lib/nautionette-temporal/runs');"
                    "root=root/os.environ['MAINTENANCE_RUN_ID'];s=root/'state.json';"
                    "print('STATE',s.read_text() if s.exists() else 'missing');"
                    "[(print('LOG',p.name),print(p.read_text()[-24000:])) for p in root.glob('*.log')]",
                ]
            )
            # This job only runs on random disposable fixture data. Even its
            # generated DB password is redacted before printing diagnostics.
            print(
                result.output.decode(errors="replace").replace(fixture.env["POSTGRES_PASSWORD"], "[redacted]")
            )
        except docker.errors.NotFound:
            pass
        raise
    finally:
        try:
            job = client.containers.get(controller)
            # Live backups are never deleted. This disposable CI-only helper has
            # no Docker socket and removes only its validated random fixture run.
            run_id = next(
                item.split("=", 1)[1]
                for item in job.attrs["Config"]["Env"]
                if item.startswith("MAINTENANCE_RUN_ID=")
            )
            client.containers.run(
                image,
                [
                    "python",
                    "-c",
                    "import os,re,shutil,pathlib;r=os.environ['RUN'];a=os.environ['APP'];"
                    "assert re.fullmatch(r'[a-zA-Z0-9_-]+',r) and r.endswith('-'+a);"
                    "root=pathlib.Path('/cleanup');shutil.rmtree(root/'runs'/r);"
                    "(root/('active-'+a)).unlink(missing_ok=True);(root/'fences'/a).unlink(missing_ok=True)",
                ],
                remove=True,
                network="none",
                user="0:0",
                environment={"RUN": run_id, "APP": fixture.prefix},
                volumes={"/var/lib/nautionette-temporal": {"bind": "/cleanup", "mode": "rw"}},
            )
            job.remove(force=True)
        except docker.errors.NotFound:
            pass
        fixture.cleanup()
        try:
            client.networks.get(internal).remove()
        except docker.errors.NotFound:
            pass


if __name__ == "__main__":
    asyncio.run(run())
