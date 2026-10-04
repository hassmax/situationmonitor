"""Temporary: GitHub Models in detail (what really answers), plus OpenRouter's free-model list."""
import json, os, time, requests
tok = os.environ.get("GITHUB_MODELS_TOKEN", "")
print("token present:", bool(tok), "length class:", len(tok) > 20)
for url in ["https://models.github.ai/inference/chat/completions", "https://models.inference.ai.azure.com/chat/completions"]:
    for model in ["openai/gpt-4.1", "gpt-4.1", "openai/gpt-4o-mini"]:
        body = {"model": model, "max_tokens": 50, "temperature": 0, "messages": [{"role": "user", "content": 'Reply with {"ok": true}'}]}
        try:
            r = requests.post(url, headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json", "Accept": "application/json"},
                              json=body, timeout=60, allow_redirects=False)
            print(url, model, r.status_code, r.headers.get("content-type"), repr(r.text[:250]), {k: v for k, v in r.headers.items() if k.lower().startswith(("x-ratelimit", "x-ms", "location", "server"))})
        except Exception as e:
            print(url, model, "ERR", e)
        time.sleep(2)
big = "Count the words. " + ("lorem ipsum dolor sit amet " * 3200)
r = requests.post("https://models.github.ai/inference/chat/completions", headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                  json={"model": "openai/gpt-4.1", "max_tokens": 50, "messages": [{"role": "user", "content": big}]}, timeout=120)
print("big:", r.status_code, repr(r.text[:300]))
r = requests.get("https://models.github.ai/catalog/models", headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}, timeout=30)
print("catalog:", r.status_code, r.headers.get("content-type"), repr(r.text[:300]))
try:
    for m in r.json():
        if any(k in m.get("id", "") for k in ("gpt-4.1", "gpt-5", "gpt-4o", "deepseek", "grok", "llama")):
            print("  ", m.get("id"), m.get("rate_limit_tier"), m.get("limits"))
except Exception:
    pass
r = requests.get("https://openrouter.ai/api/v1/models", timeout=30)
try:
    free = [m for m in r.json()["data"] if m["id"].endswith(":free")]
    print("openrouter free models:", len(free))
    for m in sorted(free, key=lambda m: -(m.get("context_length") or 0))[:25]:
        print("  ", m["id"], m.get("context_length"))
except Exception as e:
    print("openrouter:", r.status_code, e)
# r2
