use serde::Serialize;
use tokio::runtime::Runtime;

fn main() {
    let _ = Runtime::new();
}

#[derive(Serialize)]
struct Reply;
