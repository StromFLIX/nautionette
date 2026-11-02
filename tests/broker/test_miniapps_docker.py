"""Real offline compiler -> scratch -> SQLite persistence and failed-release rollback.

Uses the actual broker HTTP routes, a named volume and isolated Docker networks.
Never needs a model, gateway, workflow engine or external runtime network.
"""

import ipaddress
import json
import os
import socket
import time
import uuid
from pathlib import Path

import docker
import httpx
import pytest
from nautionette_docker_broker import image_context

pytestmark = pytest.mark.skipif(
    os.environ.get("NAUTIONETTE_DOCKER_TESTS") != "1",
    reason="requires Docker 28+ and CI-built broker/compiler images",
)
ROOT = Path(__file__).resolve().parents[2]


def test_real_scratch_runtime_persistence_isolation_and_rollback():
    client = docker.from_env()
    assert int(client.version()["Version"].split(".")[0]) >= 28, "Isolated gateways require Docker 28+"
    fingerprint = image_context.context_hash(ROOT / "services/miniapp-sdk")
    client.images.get("nautionette/miniapp-builder:dev").tag(f"nautionette/miniapp-builder:{fingerprint}")
    namespace = "miniapp-test-" + uuid.uuid4().hex
    volume = client.volumes.create(name=namespace)
    broker = None
    try:
        broker = client.containers.run(
            "nautionette/docker-broker:dev",
            detach=True,
            entrypoint=[
                "uvicorn",
                "nautionette_docker_broker.main:app",
                "--host",
                "0.0.0.0",  # noqa: S104 - inside test container; host port is loopback-only
                "--port",
                "9100",
                "--lifespan",
                "off",
            ],
            environment={
                "INTERNAL_TOKEN": "integration-test-internal",
                "WORKFLOWS_VOLUME": namespace,
                "AGENT_IMAGE_REGISTRY_PREFIX": "",
                "MINIAPP_TEST_DIAGNOSTICS": "1",
            },
            volumes={
                "/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"},
                volume.name: {"bind": "/miniapps", "mode": "rw"},
            },
            ports={"9100/tcp": ("127.0.0.1", None)},
        )
        broker.reload()
        port = broker.attrs["NetworkSettings"]["Ports"]["9100/tcp"][0]["HostPort"]
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            headers={"X-Internal-Token": "integration-test-internal"},
            timeout=240,
        ) as http:
            for _ in range(60):
                try:
                    if http.get("/miniapps/counter").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
            else:
                pytest.fail("Broker did not start: " + broker.logs(tail=40).decode())
            source = (ROOT / "services/miniapp-sdk/src/app.rs").read_text()
            started = time.monotonic()
            reply = http.post(
                "/miniapps/counter/deploy",
                json={"revision": 1, "source": source, "memory_mb": 64, "mode": "on-demand"},
            )
            assert reply.status_code == 200, reply.text + "\n" + broker.logs(tail=50).decode()
            metrics = reply.json()
            assert metrics["revision"] == 1 and metrics["memory_bytes"] > 0
            assert metrics["memory_bytes"] < metrics["memory_limit_bytes"] == 64 * 1024 * 1024
            assert 0 < metrics["image_bytes"] < 16 * 1024 * 1024
            print(json.dumps({"build_seconds": time.monotonic() - started, **metrics}))

            active_revision = 1

            def rpc(path, method="GET"):
                result = http.post(
                    "/miniapps/counter/request",
                    json={
                        "revision": active_revision,
                        "request": {
                            "path": path,
                            "method": method,
                            "body": None,
                            "request_id": uuid.uuid4().hex,
                        },
                    },
                )
                assert result.status_code == 200, result.text
                return result.json()

            assert rpc("/state") == {"value": 0}
            assert rpc("/increment", "POST") == {"value": 1}
            assert http.post("/miniapps/counter/control", json={"action": "restart"}).status_code == 200
            assert rpc("/state") == {"value": 1}
            # A generated health/migration function may corrupt its private copy;
            # a failed release must not corrupt the previous app's SQLite state.
            broken = (
                source[: source.index("pub fn health")]
                + """
pub fn health(ctx:&mut Context)->Result<()> {
    ctx.db.execute_batch("DROP TABLE IF EXISTS counter;")?;
    Err("broken migration".into())
}
pub fn handle(_: &mut Context,_:&Request)->Result<Value>{Ok(json!({}))}
"""
            )
            failed = http.post(
                "/miniapps/counter/deploy",
                json={"revision": 2, "source": broken, "memory_mb": 64, "mode": "on-demand"},
            )
            assert failed.status_code == 422, failed.text
            assert http.get("/miniapps/counter").json()["revision"] == 1
            assert rpc("/state") == {"value": 1}
            restored = http.post(
                "/miniapps/counter/deploy",
                json={"revision": 3, "source": source, "memory_mb": 64, "mode": "on-demand"},
            )
            assert restored.status_code == 200, restored.text
            active_revision = 3
            assert rpc("/state") == {"value": 1}
            runtimes = client.containers.list(
                all=True,
                filters={"label": [f"nautionette.deployment={namespace}", "nautionette.miniapp=counter"]},
            )
            assert len(runtimes) == 1
            active = runtimes[0]
            active.reload()
            policy = active.attrs["HostConfig"]
            assert policy["ReadonlyRootfs"] and not policy["Privileged"]
            assert policy["CapDrop"] == ["ALL"] and policy["NanoCpus"] == 250000000
            assert len(active.attrs["Mounts"]) == 3
            assert all(
                m["Destination"] in ["/data/state.db", "/data/state.db-wal", "/data/state.db-shm"]
                for m in active.attrs["Mounts"]
            )
            # Docker adds a default PATH even to FROM scratch. It conveys no
            # credentials; every other environment key must be app-scoped.
            assert {v.split("=", 1)[0] for v in active.attrs["Config"]["Env"]} == {
                "MINIAPP_TOKEN",
                "PATH",
            }
            networks = list(active.attrs["NetworkSettings"]["Networks"])
            assert len(networks) == 1
            net = client.networks.get(networks[0])
            assert (
                net.attrs["Internal"]
                and net.attrs["Options"]["com.docker.network.bridge.gateway_mode_ipv4"] == "isolated"
            )
            # Actual sockets and syscalls, not only Docker metadata. A live host
            # listener would be reachable on an ordinary non-isolated bridge.
            probes = """use crate::{Context,Request,Result,Value,json};
use std::os::fd::AsRawFd;
unsafe extern "C" {
 fn fallocate(fd:i32,mode:i32,offset:i64,len:i64)->i32;
 fn ioctl(fd:i32,request:u64,...)->i32;
 fn fsetxattr(fd:i32,name:*const std::ffi::c_char,value:*const u8,len:usize,flags:i32)->i32;
}
pub fn health(_: &mut Context)->Result<()> {Ok(())}
pub fn handle(_: &mut Context,req:&Request)->Result<Value>{
 let timeout=std::time::Duration::from_secs(1);
 let remote=std::net::TcpStream::connect_timeout(&"1.1.1.1:443".parse()?,timeout).is_ok();
 let host=std::net::TcpStream::connect_timeout(&req.body["host"].as_str().unwrap().parse()?,timeout).is_ok();
 let bridge=std::net::TcpStream::connect("miniapp-host:9100").is_ok();
 let file=std::fs::OpenOptions::new().write(true).open("/data/state.db")?;
 let denied=||std::io::Error::last_os_error().raw_os_error()==Some(1);
 let allocation=unsafe{fallocate(file.as_raw_fd(),1,0,32*1024*1024)}==-1 && denied();
 let mut available=0i32;
 let ioctl_denied=unsafe{ioctl(file.as_raw_fd(),0x541Bu64,&mut available as *mut i32)}==-1 && denied();
 let xattr=unsafe{fsetxattr(file.as_raw_fd(),c"user.probe".as_ptr(),b"x".as_ptr(),1,0)}==-1 && denied();
 Ok(json!({"remote":remote,"host":host,"bridge":bridge,
    "allocation_denied":allocation,"ioctl_denied":ioctl_denied,"xattr_denied":xattr,
    "other_data":std::path::Path::new("/miniapps/counter/3/state.db").exists(),
    "shell":std::path::Path::new("/bin/sh").exists(),
    "instance_token":std::env::var("APP_TOKEN").is_ok(),
    "ipv6_disabled":std::fs::read_to_string("/proc/sys/net/ipv6/conf/all/disable_ipv6")?.trim()=="1"}))}
"""
            reply = http.post(
                "/miniapps/probe/deploy",
                json={
                    "revision": 1,
                    "source": probes,
                    "memory_mb": 64,
                    "mode": "on-demand",
                },
            )
            assert reply.status_code == 200, reply.text
            probe = client.containers.list(
                filters={"label": ["nautionette.miniapp=probe", f"nautionette.deployment={namespace}"]}
            )[0]
            probe.reload()
            probe_net = client.networks.get(next(iter(probe.attrs["NetworkSettings"]["Networks"])))
            subnet = probe_net.attrs["IPAM"]["Config"][0]
            gateway = subnet.get("Gateway") or str(ipaddress.ip_network(subnet["Subnet"])[1])
            with socket.create_server(("0.0.0.0", 0)) as host:  # noqa: S104 - ephemeral CI isolation probe
                response = http.post(
                    "/miniapps/probe/request",
                    json={
                        "revision": 1,
                        "request": {
                            "method": "GET",
                            "path": "/probe",
                            "body": {"host": f"{gateway}:{host.getsockname()[1]}"},
                        },
                    },
                )
            assert response.status_code == 200, response.text
            assert response.json() == {
                "remote": False,
                "host": False,
                "bridge": True,
                "allocation_denied": True,
                "ioctl_denied": True,
                "xattr_denied": True,
                "other_data": False,
                "shell": False,
                "instance_token": False,
                "ipv6_disabled": True,
            }
            assert http.post("/miniapps/counter/control", json={"action": "suspend"}).status_code == 200
            assert (
                http.post(
                    "/miniapps/counter/request",
                    json={"revision": active_revision, "request": {"method": "GET", "path": "/state"}},
                ).status_code
                == 422
            )
            assert http.post("/miniapps/counter/control", json={"action": "resume"}).status_code == 200
            assert rpc("/state") == {"value": 1}
            assert http.delete("/miniapps/counter").status_code == 200
            assert http.delete("/miniapps/probe").status_code == 200
    finally:
        for container in client.containers.list(
            all=True, filters={"label": f"nautionette.deployment={namespace}"}
        ):
            container.remove(force=True)
        if broker:
            broker.remove(force=True)
        for network in client.networks.list(filters={"label": f"nautionette.deployment={namespace}"}):
            network.remove()
        for image in client.images.list(filters={"label": f"nautionette.deployment={namespace}"}):
            client.images.remove(image.id, force=True)
        volume.remove(force=True)
        client.close()
