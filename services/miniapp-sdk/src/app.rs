//! Example module: durable state without a workflow or model call.
use crate::{Context, Request, Result, Value, json};
pub fn health(ctx: &mut Context) -> Result<()> {
    ctx.db.execute_batch("CREATE TABLE IF NOT EXISTS counter (id INTEGER PRIMARY KEY CHECK(id=1), value INTEGER NOT NULL); INSERT OR IGNORE INTO counter VALUES(1,0);")?;
    Ok(())
}
pub fn handle(ctx: &mut Context, req: &Request) -> Result<Value> {
    if req.method == "POST" && req.path == "/increment" {
        ctx.db
            .execute("UPDATE counter SET value = value + 1 WHERE id=1", [])?;
    } else if req.method != "GET" || req.path != "/state" {
        return Err("Unknown operation".into());
    }
    let value: i64 = ctx
        .db
        .query_row("SELECT value FROM counter WHERE id=1", [], |r| r.get(0))?;
    Ok(json!({"value":value}))
}
