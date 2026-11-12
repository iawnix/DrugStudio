# Agent control plane

DrugStudio uses two model roles with one auditable control boundary:

```text
human -> chat supervisor -> workflow control plane -> scientific stage agents -> tools
```

The chat supervisor can inspect the selected project and run, explain evidence,
and propose lifecycle actions. `request_workflow_action` is the only chat tool
for starting, pausing, cancelling, or branching a run. It is a mutating tool,
so the chat turn is suspended and the workbench asks a human to approve it.
The approved request then calls the same control methods used by the workflow
UI. The event records both the human approval and `chat_supervisor` as the
requesting actor.

The supervisor must not edit manifests, event logs, lock files, or control
files through shell commands. Scientific stage agents do not receive project
chat tools or lifecycle controls. They operate only through their stage
contracts and persist outputs as artifacts. A running workflow keeps its model
and parameters fixed; later model settings apply to new runs.

The two roles share a connection (API URL and key) by default, but use separate
model names and separate context. The shared research context is the typed
manifest snapshot, artifact provenance, event stream, and human decisions. Raw
chat history is not passed to the workflow agent, and workflow internal
messages are not copied into the chat history.
