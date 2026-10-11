"""One entry point for every model call the builder makes. Every call is written to the trace.

Backend: the Anthropic SDK when ANTHROPIC_API_KEY is set, otherwise claude-agent-sdk
(which uses the local Claude Code login). Both return plain text."""
import asyncio, json, os, re, time

from . import trace

MODEL = os.environ.get("BUILDER_MODEL", "claude-opus-5-5")
FAST_MODEL = os.environ.get("BUILDER_FAST_MODEL", "claude-sonnet-5-5")


def _anthropic(model, system, prompt, max_tokens):
    import anthropic
    client = anthropic.Anthropic()
    with client.messages.stream(model=model, max_tokens=max_tokens, system=system or anthropic.NOT_GIVEN,
                                messages=[{"role": "user", "content": prompt}]) as s:
        msg = s.get_final_message()
    text = "".join(b.text for b in msg.content if b.type == "text")
    return text, msg.usage.input_tokens, msg.usage.output_tokens


def _agent_sdk(model, system, prompt, max_tokens):
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query

    async def run():
        opts = ClaudeAgentOptions(model=model, system_prompt=system or "", allowed_tools=[], max_turns=3,
                                  disallowed_tools=["Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebFetch",
                                                    "WebSearch", "Agent", "Task", "TodoWrite", "NotebookEdit"],
                                  setting_sources=[], permission_mode="dontAsk")
        out, usage = [], {}
        try:
            async for m in query(prompt=prompt, options=opts):
                if isinstance(m, AssistantMessage):
                    out += [b.text for b in m.content if isinstance(b, TextBlock)]
                elif isinstance(m, ResultMessage):
                    usage = m.usage or {}
        except Exception:
            # a turn-limit or result error after the answer was written: keep the text we have
            if not "".join(out).strip():
                raise
        return "".join(out), usage.get("input_tokens"), usage.get("output_tokens")

    return asyncio.run(run())


def ask(prompt, purpose, system=None, model=None, max_tokens=32000):
    """Send one prompt, return the text. `purpose` is a one-line reason that goes into the trace."""
    model = model or MODEL
    t0 = time.time()
    backend = _anthropic if os.environ.get("ANTHROPIC_API_KEY") else _agent_sdk
    text, tin, tout = backend(model, system, prompt, max_tokens)
    tr = trace.current()
    if tr:
        tr.model_call(model, purpose, time.time() - t0, tin, tout, backend=backend.__name__.strip("_"))
    return text


def ask_json(prompt, purpose, **kw):
    """Like ask(), but the answer must be one JSON object or array. Takes the first fenced or bare JSON block."""
    text = ask(prompt + "\n\nAnswer with JSON only.", purpose, **kw)
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    raw = m.group(1) if m else text[text.find("{") if "{" in text else text.find("["):]
    return json.loads(raw)


def parallel(jobs, max_workers=6):
    """Run several model calls at the same time. `jobs` is a dict name -> zero-argument function.
    Returns name -> result. Raises the first error after all jobs ended."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {k: ex.submit(f) for k, f in jobs.items()}
        out, err = {}, None
        for k, f in futs.items():
            try:
                out[k] = f.result()
            except Exception as e:
                err = err or e
    if err:
        raise err
    return out
