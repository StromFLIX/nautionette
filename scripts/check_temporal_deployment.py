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
        fixture.docker("network", "create", fixture.network)
        client.networks.create(internal)
        fixture.postgres("postgres")
        legacy = fixture.build("1.29.7", "legacy")
        old, address = fixture.start(legacy)
        client.networks.get(internal).connect(old, aliases=["temporal"])
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
                    http if service == "backend" else idle,
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
        new, address = fixture.start(final_image, fence=(fixture.prefix, commit))
        client.networks.get(internal).connect(new, aliases=["temporal"])
        for container in services.values():
            container.start()
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
            # Backups stay private during a live rollout; disposable CI removes
            # only this fixture's run directory through its own controller.
            job.exec_run(
                [
                    "python",
                    "-c",
                    "import shutil,os;root='/var/lib/nautionette-temporal/';"
                    "shutil.rmtree(root+'runs/'+os.environ['MAINTENANCE_RUN_ID']);"
                    "os.unlink(root+'active-'+os.environ['MAINTENANCE_APP'])",
                ]
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
