// Build both images in one BuildKit graph. The named context passes the base
// directly to the agent-set target; no mutable registry/dev alias or second
// legacy builder is needed. Both outputs are loaded for the offline CI tests.
group "default" {
  targets = ["pi-base", "pi-agent-default"]
}

target "pi-base" {
  context = "images/pi-base"
  tags = ["nautionette/pi-base:dev"]
  cache-from = ["type=gha,scope=pi-base"]
  cache-to = ["type=gha,mode=max,scope=pi-base"]
}

target "pi-agent-default" {
  context = "images/agent-sets/default"
  contexts = { pi-base = "target:pi-base" }
  args = { BASE_IMAGE = "pi-base" }
  tags = ["nautionette/pi-agent-default:dev"]
  cache-from = ["type=gha,scope=pi-agent-default"]
  cache-to = ["type=gha,mode=max,scope=pi-agent-default"]
}
