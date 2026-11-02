//! Platform-owned HTTP/SQLite host. Only app.rs is supplied by the publishing agent.
pub use rusqlite;
use serde::Deserialize;
pub use serde_json::{Value, json};
use std::{
    env,
    io::{Read, Write},
    net::{TcpListener, TcpStream, ToSocketAddrs},
    time::Duration,
};
mod app;

pub type Result<T> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;
const LIMIT: usize = 65536;
#[derive(Deserialize)]
pub struct Request {
    pub method: String,
    pub path: String,
    #[serde(default)]
    pub body: Value,
    #[serde(default)]
    pub request_id: String,
}
pub struct Context {
    pub db: rusqlite::Connection,
}
impl Context {
    /// A named, server-authorized MCP call. No upstream or instance credentials enter this process.
    pub fn mcp(&self, tool: &str, arguments: Value, request_id: &str) -> Result<Value> {
        self.capability(
            json!({"kind":"mcp", "tool":tool, "arguments":arguments, "request_id":request_id}),
        )
    }
    /// HTTPS only, public IPs only, exact manifest origins/methods/path prefixes; never follows redirects.
    pub fn http(&self, url: &str, method: &str, body: Value, request_id: &str) -> Result<Value> {
        self.capability(json!({"kind":"http", "url":url, "method":method, "body":body, "request_id":request_id}))
    }
    fn capability(&self, payload: Value) -> Result<Value> {
        let token = env::var("MINIAPP_TOKEN")?;
        let bytes = serde_json::to_vec(&payload)?;
        if bytes.len() > LIMIT {
            return Err("Capability request too large".into());
        }
        let mut stream = TcpStream::connect_timeout(
            &"miniapp-host:9100"
                .to_socket_addrs()?
                .next()
                .ok_or("App host unavailable")?,
            Duration::from_secs(5),
        )?;
        stream.set_read_timeout(Some(Duration::from_secs(30)))?;
        stream.set_write_timeout(Some(Duration::from_secs(5)))?;
        write!(
            stream,
            "POST /miniapps/capability HTTP/1.1\r\nHost: miniapp-host\r\nAuthorization: Bearer {token}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
            bytes.len()
        )?;
        stream.write_all(&bytes)?;
        let mut reply = Vec::new();
        stream.take((LIMIT + 8193) as u64).read_to_end(&mut reply)?;
        if reply.len() > LIMIT + 8192 {
            return Err("Capability response too large".into());
        }
        let split = reply
            .windows(4)
            .position(|w| w == b"\r\n\r\n")
            .ok_or("Invalid capability response")?;
        let header = std::str::from_utf8(&reply[..split])?;
        if !header.starts_with("HTTP/1.1 200 ") {
            return Err("Capability denied or unavailable; inspect app activity".into());
        }
        Ok(serde_json::from_slice(&reply[split + 4..])?)
    }
}

fn serve(mut stream: TcpStream, ctx: &mut Context, token: &str) -> Result<()> {
    stream.set_read_timeout(Some(Duration::from_secs(5)))?;
    stream.set_write_timeout(Some(Duration::from_secs(5)))?;
    let mut bytes = Vec::new();
    let split;
    loop {
        if bytes.len() > 4096 {
            return Err("Headers too large".into());
        }
        let mut part = [0; 1024];
        let n = stream.read(&mut part)?;
        if n == 0 {
            return Err("Incomplete request".into());
        }
        bytes.extend_from_slice(&part[..n]);
        if let Some(pos) = bytes.windows(4).position(|w| w == b"\r\n\r\n") {
            split = pos;
            break;
        }
    }
    let head = std::str::from_utf8(&bytes[..split])?;
    let mut lines = head.split("\r\n");
    let route = lines.next().unwrap_or("");
    let headers: Vec<_> = lines
        .filter_map(|s| s.split_once(':'))
        .map(|(k, v)| (k.to_ascii_lowercase(), v.trim()))
        .collect();
    if headers
        .iter()
        .filter(|(k, v)| k == "authorization" && *v == format!("Bearer {token}"))
        .count()
        != 1
    {
        return response(&mut stream, 401, &json!({"error":"unauthorized"}));
    }
    if route == "GET /healthz HTTP/1.1" {
        return response(&mut stream, 200, &json!({"status":"ok"}));
    }
    if route != "POST /rpc HTTP/1.1" {
        return response(&mut stream, 404, &json!({"error":"not found"}));
    }
    let size: usize = headers
        .iter()
        .find(|(k, _)| k == "content-length")
        .ok_or("Missing content length")?
        .1
        .parse()?;
    if size > LIMIT {
        return response(&mut stream, 413, &json!({"error":"too large"}));
    }
    let start = split + 4;
    while bytes.len() < start + size {
        let mut part = [0; 4096];
        let remaining = (start + size - bytes.len()).min(part.len());
        let n = stream.read(&mut part[..remaining])?;
        if n == 0 {
            return Err("Incomplete body".into());
        }
        bytes.extend_from_slice(&part[..n]);
    }
    let req: Request = serde_json::from_slice(&bytes[start..start + size])?;
    match app::handle(ctx, &req) {
        Ok(data) => response(&mut stream, 200, &data),
        Err(_) => response(
            &mut stream,
            500,
            &json!({"error":"Application request failed"}),
        ),
    }
}
fn response(stream: &mut TcpStream, status: u16, value: &Value) -> Result<()> {
    let mut bytes = serde_json::to_vec(value)?;
    let status = if bytes.len() > LIMIT {
        bytes = b"{\"error\":\"Application response too large\"}".to_vec();
        413
    } else {
        status
    };
    write!(
        stream,
        "HTTP/1.1 {status} Result\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        bytes.len()
    )?;
    stream.write_all(&bytes)?;
    Ok(())
}
fn main() -> Result<()> {
    let token = env::var("MINIAPP_TOKEN")?;
    let path = env::var("MINIAPP_DB").unwrap_or("/data/state.db".into());
    let db = rusqlite::Connection::open(path)?;
    db.busy_timeout(Duration::from_secs(5))?;
    // These three individual files are mounted into a read-only root. Persistent
    // WAL prevents SQLite trying to unlink mountpoints at final connection close.
    let mut persist: std::ffi::c_int = 1;
    unsafe {
        let result = rusqlite::ffi::sqlite3_file_control(
            db.handle(),
            c"main".as_ptr(),
            rusqlite::ffi::SQLITE_FCNTL_PERSIST_WAL,
            (&mut persist as *mut std::ffi::c_int).cast(),
        );
        if result != rusqlite::ffi::SQLITE_OK {
            return Err("Persistent WAL unavailable".into());
        }
    }
    db.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA max_page_count=3072; PRAGMA wal_autocheckpoint=32;")?;
    let mut ctx = Context { db };
    app::health(&mut ctx)?;
    let listener = TcpListener::bind("0.0.0.0:8080")?;
    for stream in listener.incoming().flatten() {
        // Request diagnostics can contain application data; don't log them.
        let _ = serve(stream, &mut ctx, &token);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn exchange(request: String) -> String {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        let task = std::thread::spawn(move || {
            let mut ctx = Context {
                db: rusqlite::Connection::open_in_memory().unwrap(),
            };
            app::health(&mut ctx).unwrap();
            serve(listener.accept().unwrap().0, &mut ctx, "test-app-token").unwrap();
        });
        let mut stream = TcpStream::connect(addr).unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        stream.write_all(request.as_bytes()).unwrap();
        let mut reply = String::new();
        stream.read_to_string(&mut reply).unwrap();
        task.join().unwrap();
        reply
    }

    #[test]
    fn health_requires_app_credential() {
        assert!(exchange("GET /healthz HTTP/1.1\r\n\r\n".into()).starts_with("HTTP/1.1 401"));
        let result = exchange(
            "GET /healthz HTTP/1.1\r\nAuthorization: Bearer test-app-token\r\n\r\n".into(),
        );
        assert!(result.starts_with("HTTP/1.1 200"));
        assert!(result.ends_with("{\"status\":\"ok\"}"));
    }

    #[test]
    fn authenticated_rpc_uses_bundled_sqlite() {
        let body = r#"{"method":"POST","path":"/increment","request_id":"sample"}"#;
        let result = exchange(format!(
            "POST /rpc HTTP/1.1\r\nAuthorization: Bearer test-app-token\r\nContent-Length: {}\r\n\r\n{}",
            body.len(),
            body
        ));
        assert!(result.starts_with("HTTP/1.1 200"));
        assert!(result.ends_with("{\"value\":1}"));
    }

    #[test]
    fn oversized_request_is_rejected_before_body_read() {
        let result = exchange("POST /rpc HTTP/1.1\r\nAuthorization: Bearer test-app-token\r\nContent-Length: 65537\r\n\r\n".into());
        assert!(result.starts_with("HTTP/1.1 413"));
    }
}
