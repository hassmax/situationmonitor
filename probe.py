"""Temporary: free AI providers from GitHub's servers: GitHub Models for real, the others' published limits."""
import json, os, re, time, requests
tok = os.environ.get("GITHUB_MODELS_TOKEN", "")
GH = "https://models.github.ai/inference/chat/completions"

def call(model, text, max_tokens=200, json_mode=True):
    body = {"model": model, "max_tokens": max_tokens, "temperature": 0,
            "messages": [{"role": "user", "content": text}]}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    t = time.time()
    r = requests.post(GH, headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}, json=body, timeout=120)
    hdr = {k: v for k, v in r.headers.items() if "ratelimit" in k.lower() or "limit" in k.lower()}
    return r.status_code, round(time.time() - t, 1), r.text[:300].replace("\n", " "), hdr

print("== GitHub Models catalog")
r = requests.get("https://models.github.ai/catalog/models", headers={"Authorization": f"Bearer {tok}"}, timeout=30)
print(r.status_code)
try:
    cat = r.json()
    for m in cat:
        lim = m.get("limits") or {}
        print(f"  {m.get('id'):45} tier={m.get('rate_limit_tier')} in={lim.get('max_input_tokens')} out={lim.get('max_output_tokens')}")
except Exception as e:
    print("  catalog not json", r.text[:200])

print("== GitHub Models small calls")
for m in ["openai/gpt-4.1", "openai/gpt-4o", "openai/gpt-5", "openai/gpt-5-mini", "openai/gpt-4.1-mini", "deepseek/deepseek-r1", "meta/llama-4-maverick-17b-128e-instruct-fp8", "xai/grok-3"]:
    print(m, call(m, 'Reply with the JSON object {"ok": true} and nothing else.'))
    time.sleep(1)
big = "Count the words. " + ("lorem ipsum dolor sit amet " * 3200) + ' Reply with {"ok": true}.'
print("== big request (~20k tokens)")
for m in ["openai/gpt-4.1", "openai/gpt-4.1-mini"]:
    print(m, call(m, big))
    time.sleep(1)

print("== Published limits")
for name, url, pat in [
    ("github models", "https://docs.github.com/en/github-models/use-github-models/prototyping-with-ai-models", r"(?:Requests per (?:day|minute)|Tokens per request|Concurrent)[^.]{0,200}"),
    ("groq", "https://console.groq.com/docs/rate-limits", r"(?:gpt-oss-120b|llama-3\.3-70b-versatile|qwen[\w./-]*|kimi[\w./-]*)[^<]{0,160}"),
    ("cerebras", "https://inference-docs.cerebras.ai/support/rate-limits", r"(?:gpt-oss-120b|llama-3\.3-70b|qwen-3-235b[\w-]*|llama3\.1-8b)[^<]{0,200}"),
    ("cerebras pricing", "https://www.cerebras.ai/pricing", r"(?:[Ff]ree[^<]{0,200})"),
    ("openrouter limits", "https://openrouter.ai/docs/api-reference/limits", r"(?:free[^<]{0,240})"),
    ("mistral", "https://docs.mistral.ai/deployment/laplateforme/tier/", r"(?:[Ff]ree|[Ee]xperiment)[^<]{0,200}"),
]:
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        text = re.sub(r"\s+", " ", re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", r.text, flags=re.S))
        text = re.sub(r"<[^>]+>", " | ", text)
        hits = re.findall(pat, text)
        print(f"-- {name}: HTTP {r.status_code}, {len(hits)} hits")
        for h in hits[:10]:
            print("   ", re.sub(r"(\s*\|\s*)+", " | ", h)[:240])
    except Exception as e:
        print(f"-- {name}: {e}")
