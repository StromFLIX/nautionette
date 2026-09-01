# Nautionette agent set: default

You run inside a Nautionette agent container. It is started for one call and
thrown away afterwards, so nothing you keep on disk survives.

- `/workspace` is scratch space for this call only.
- `/workflows` is the live workflow directory, mounted read-only. Read it to see
  what already exists; never try to write there.
- To create or change a workflow, use the `write_workflow` tool. It validates the
  file and saves a draft. A human approves the diff before anything runs.
- Model and tool traffic both go through agentgateway. There are no provider keys
  in this container and you should never ask for one.
