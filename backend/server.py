import os
import base64
import json
import re
import subprocess
import socket
import uuid
import datetime
import requests
import pyautogui
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SYSTEM_PROMPT = "You are a helpful AI assistant analyzing a screenshot of the user's screen. Be concise and direct."

ollama_model = os.getenv("OLLAMA_MODEL", "gemma3:12b")
gemini_api_key_raw = os.getenv("GEMINI_API_KEY", "")
gemini_api_key = gemini_api_key_raw if gemini_api_key_raw and gemini_api_key_raw != "put_your_gemini_api_key_here" else ""
gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
openai_api_key_raw = os.getenv("OPENAI_API_KEY", "")
openai_api_key = openai_api_key_raw if openai_api_key_raw and openai_api_key_raw != "put_your_openai_api_key_here" else ""
openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

BLOCKED_PATTERNS = [
    "sudo", "su ",
    " rm ", " rm -rf ", "rm -rf ", " rm -r ", "rm -r ",
    "trash", "rmdir",
    ".env", "auth.json", "passwords", "api_key", "secret",
    r"\|\s*bash", r"\|\s*sh\s", r"\|\s*zsh", r"\|\s*fish",
    "curl.*|.*bash", "wget.*|.*bash",
    "mail ", "sendmail", "email",
    "payment", "checkout", "charge", "stripe",
    "discord", "slack", "telegram", "webhook",
    "say ", "speak",
]

VALID_ACTION_TYPES = {"click", "type", "hotkey", "open_app", "run_command", "none"}

CONTROL_PLAN_PROMPT = """You are a Mac AI assistant that can see the user's screen. Your job is to analyze the screenshot and suggest ONE concrete, safe, useful next action.

Return ONLY valid JSON with no markdown formatting, no code fences, no extra text. Just the raw JSON object. Do not escape single quotes (') inside strings — they are valid as-is.

The JSON must follow this exact structure:
{
  "summary": "Short 1-2 sentence explanation of what you see on screen.",
  "recommended_action": {
    "type": "click | type | hotkey | open_app | run_command | none",
    "params": {},
    "reason": "Why this action helps the user.",
    "risk": "low | medium | high"
  },
  "requires_confirmation": true
}

Rules for picking action:
- click: needs {"x": int, "y": int}
- type: needs {"text": "string"}
- hotkey: needs {"keys": ["key1", "key2", ...]}
- open_app: needs {"name": "AppName"}
- run_command: needs {"command": "shell command"}
- none: use when no clear action makes sense (params can be empty)

CRITICAL: When you see a specific error or problem, do NOT just say "none". Recommend a concrete action:
- If a macOS permission popup is visible (microphone, camera, accessibility, etc.), set type to "none" and explain in the summary that the user should handle the popup manually.
- If an app error / "Cannot read properties of undefined" / JS error / crash is visible, recommend a useful low-risk action:
  * hotkey command+option+i to open browser/electron devtools (low risk)
  * run_command "open -a /Applications/Utilities/Console.app" to check system logs (low risk)
  * run_command "cd /path/to/project && grep -R \"error string\" --include='*.js' --include='*.ts' --include='*.py'" (low risk)
  * run_command "npm run dev" or "npm run build" to see fresh logs (medium risk)
  * open_app "Terminal" if user needs to run commands manually (low risk)
  * type with common search or fix text (low risk)
- If a terminal/build error is visible, suggest the fix command (e.g. "npm install" if missing module).
- If no action is safe but the problem is clear, set type to "none" but write a useful summary explaining exactly what the user should do next.

Safety rules:
- Never suggest sudo, rm, curl|bash, or commands touching .env/auth/passwords
- Never suggest sending messages, emails, or payments
- Risk low = harmless (typing, opening app, devtools)
- Risk medium = could affect work (running commands, npm install)
- Risk high = destructive potential (deleting, installing, sudo) -- never suggest these
- Always set requires_confirmation to true
- If unsure, set type to "none"

Examples:
{"summary": "macOS permission popup asking for audio recording access.", "recommended_action": {"type": "none", "params": {}, "reason": "Permission popup requires manual user interaction.", "risk": "low"}, "requires_confirmation": true}

{"summary": "Terminal showing 'npm ERR! Cannot read properties of undefined' error.", "recommended_action": {"type": "run_command", "params": {"command": "grep -R \"Cannot read properties of undefined\" --include='*.js' --include='*.ts' ."}, "reason": "Search project for the error to find the source.", "risk": "low"}, "requires_confirmation": true}

{"summary": "Browser showing JavaScript console error.", "recommended_action": {"type": "hotkey", "params": {"keys": ["command", "option", "i"]}, "reason": "Open developer tools to inspect the error.", "risk": "low"}, "requires_confirmation": true}

{"summary": "VS Code with code visible, no errors.", "recommended_action": {"type": "hotkey", "params": {"keys": ["command", "shift", "p"]}, "reason": "Open command palette.", "risk": "low"}, "requires_confirmation": true}

{"summary": "Desktop with no applications open.", "recommended_action": {"type": "none", "params": {}, "reason": "Nothing actionable on screen.", "risk": "low"}, "requires_confirmation": true}"""


class ChatRequest(BaseModel):
    prompt: str
    screenshot: str | None = None
    search_web: bool = False


class PlanRequest(BaseModel):
    screenshot: str


class ClickRequest(BaseModel):
    x: int
    y: int


class TypeRequest(BaseModel):
    text: str


class HotkeyRequest(BaseModel):
    keys: list[str]


class OpenAppRequest(BaseModel):
    name: str


class RunCommandRequest(BaseModel):
    command: str


class OpenCodeRunRequest(BaseModel):
    project_path: str
    task: str
    screenshot_base64: str | None = None


class SmartFixRequest(BaseModel):
    project_path: str
    project_name: str
    screenshot_base64: str
    user_prompt: str | None = None


class TestRunRequest(BaseModel):
    project_path: str
    command: str


def web_search(query: str, num: int = 5) -> str:
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=num))
        if not results:
            return "No results found."
        lines = []
        for r in results:
            title = r.get("title", "")
            body = r.get("body", "")
            href = r.get("href", "")
            lines.append(f"- {title}: {body} ({href})")
        return "\n".join(lines[:num])
    except Exception as e:
        return f"Search error: {e}"


def ollama_available():
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=2)
        return resp.status_code == 200
    except Exception:
        return False


def call_ollama(prompt: str, screenshot_b64: str | None, search_web: bool, system_override: str | None = None) -> str:
    if not ollama_available():
        raise Exception("Ollama is not running. Start it with: ollama serve")

    system = system_override or SYSTEM_PROMPT
    if search_web:
        system += "\n\nYou can search the web for current information."

    search_results = ""
    if search_web:
        search_results = web_search(prompt)

    messages = [{"role": "system", "content": system}]
    user_content = prompt
    if search_results:
        user_content = f"Web search results:\n{search_results}\n\nUser question: {prompt}"
    user_msg = {"role": "user", "content": user_content}
    if screenshot_b64:
        user_msg["images"] = [screenshot_b64]
    messages.append(user_msg)

    resp = requests.post(
        "http://localhost:11434/api/chat",
        json={"model": ollama_model, "messages": messages, "stream": False},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def call_gemini(prompt: str, screenshot_b64: str | None, search_web: bool, system_override: str | None = None) -> str:
    import google.genai as genai
    from google.genai.types import Part, Content

    client = genai.Client(api_key=gemini_api_key)
    text = (system_override or SYSTEM_PROMPT) + "\n\n" + prompt

    if search_web:
        results = web_search(prompt)
        text += f"\n\nWeb search results:\n{results}"

    parts = [Part.from_text(text=text)]
    if screenshot_b64:
        parts.append(
            Part.from_bytes(
                data=base64.b64decode(screenshot_b64),
                mime_type="image/png",
            )
        )

    resp = client.models.generate_content(
        model=gemini_model, contents=Content(parts=parts)
    )
    return resp.text


def call_openai(prompt: str, screenshot_b64: str | None, search_web: bool, system_override: str | None = None) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=openai_api_key)
    system = system_override or SYSTEM_PROMPT

    content = [{"type": "text", "text": prompt}]
    if search_web:
        results = web_search(prompt)
        content.insert(0, {"type": "text", "text": f"Web search results:\n{results}"})

    if screenshot_b64:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"},
        })

    resp = client.chat.completions.create(
        model=openai_model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": content}],
    )
    return resp.choices[0].message.content


def call_ai(screenshot_b64: str, prompt: str, system_override: str | None = None, search_web: bool = False) -> str:
    settings = load_settings()
    mode = settings.get("mode", "auto")

    if mode == "ollama":
        if not ollama_available():
            raise Exception("Ollama is selected but not running. Start it with: ollama serve")
        return call_ollama(prompt, screenshot_b64, search_web, system_override)
    elif mode == "gemini":
        if not gemini_api_key:
            raise Exception("Gemini is selected but no API key is set. Add your key in Models settings.")
        return call_gemini(prompt, screenshot_b64, search_web, system_override)
    elif mode == "openai":
        if not openai_api_key:
            raise Exception("OpenAI is selected but no API key is set. Add your key in Models settings.")
        return call_openai(prompt, screenshot_b64, search_web, system_override)

    # Auto mode: Ollama > Gemini > OpenAI
    if ollama_available():
        return call_ollama(prompt, screenshot_b64, search_web, system_override)
    elif gemini_api_key:
        return call_gemini(prompt, screenshot_b64, search_web, system_override)
    elif openai_api_key:
        return call_openai(prompt, screenshot_b64, search_web, system_override)
    raise Exception("No AI provider available")


def extract_json(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        start = 0
        for i, line in enumerate(lines):
            if "```" in line:
                start = i + 1
                break
        lines = lines[start:]
        end_lines = []
        for line in lines:
            if "```" in line:
                break
            end_lines.append(line)
        text = "\n".join(end_lines).strip()

    # Fix common AI JSON mistakes: \' is not valid JSON, unescape to '
    text = text.replace("\\'", "'")

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r'\{\s*"summary".*?\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return None


def validate_plan(data: dict) -> dict | None:
    if not isinstance(data, dict) or "summary" not in data:
        return None
    action = data.get("recommended_action")
    if not isinstance(action, dict) or "type" not in action:
        return None
    if action["type"] not in VALID_ACTION_TYPES:
        action["type"] = "none"
    if not isinstance(action.get("params"), dict):
        action["params"] = {}
    action.setdefault("reason", "")
    action.setdefault("risk", "low")
    data.setdefault("requires_confirmation", True)
    return data


def check_command_safety(command: str) -> tuple[bool, str]:
    cmd_lower = command.lower()
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, cmd_lower):
            return False, f"Blocked: command contains '{pattern}'"
    return True, ""


@app.post("/api/chat")
async def chat(req: ChatRequest):
    try:
        msg = call_ai(req.screenshot, req.prompt, None, req.search_web)
        return {"message": msg}
    except Exception as e:
        return {"message": f"AI error: {str(e)}"}


@app.post("/api/control/plan")
async def control_plan(req: PlanRequest):
    try:
        raw = call_ai(req.screenshot, "Analyze this screenshot and return the action plan JSON.", CONTROL_PLAN_PROMPT)
        parsed = extract_json(raw)
        if parsed is None:
            return {
                "success": False,
                "summary": "The AI responded with unexpected formatting. Please try again.",
                "recommended_action": {"type": "none", "params": {}, "reason": "Could not parse AI response.", "risk": "low"},
                "requires_confirmation": True,
                "raw_response": raw[:500],
            }
        validated = validate_plan(parsed)
        if validated is None:
            return {
                "success": False,
                "summary": "The AI returned an incomplete plan.",
                "recommended_action": {"type": "none", "params": {}, "reason": "Missing required fields.", "risk": "low"},
                "requires_confirmation": True,
            }
        validated["success"] = True
        return validated
    except Exception as e:
        return {
            "success": False,
            "summary": f"Error: {str(e)}",
            "recommended_action": {"type": "none", "params": {}, "reason": "Backend error.", "risk": "low"},
            "requires_confirmation": True,
        }


@app.get("/api/providers")
async def check_providers():
    settings = load_settings()
    return {
        "ollama": {"available": ollama_available(), "model": settings.get("ollama_model", ollama_model)},
        "gemini": {"available": bool(gemini_api_key), "model": settings.get("gemini_model", gemini_model)},
        "openai": {"available": bool(openai_api_key), "model": settings.get("openai_model", openai_model)},
        "mode": settings.get("mode", "auto"),
    }


@app.get("/api/health")
async def health():
    oa = ollama_available()
    oc = find_opencode_server()
    settings = load_settings()
    mode = settings.get("mode", "auto")
    provider = "ollama" if oa else "gemini" if gemini_api_key else "openai" if openai_api_key else "none"
    return {
        "backend": True,
        "ollama": oa,
        "opencode": oc is not None,
        "provider": provider,
        "mode": mode,
        "ollama_model": settings.get("ollama_model", ollama_model),
        "gemini_model": settings.get("gemini_model", gemini_model),
        "openai_model": settings.get("openai_model", openai_model),
        "gemini_available": bool(gemini_api_key),
        "openai_available": bool(openai_api_key),
    }


@app.get("/api/setup/status")
async def setup_status():
    oa = ollama_available()
    oc = find_opencode_server()
    provider = "ollama" if oa else "gemini" if gemini_api_key else "openai" if openai_api_key else "none"

    screen_ok = False
    try:
        r = subprocess.run(
            ["screencapture", "-x", "/tmp/sompter_setup_screen.png"],
            capture_output=True, timeout=5,
        )
        screen_ok = r.returncode == 0
        if os.path.exists("/tmp/sompter_setup_screen.png"):
            os.unlink("/tmp/sompter_setup_screen.png")
    except Exception:
        pass

    access_ok = False
    try:
        pyautogui.position()
        access_ok = True
    except Exception:
        pass

    return {
        "screen_recording": screen_ok,
        "accessibility": access_ok,
        "backend": True,
        "ollama": oa,
        "opencode": oc is not None,
        "provider": provider,
    }


@app.post("/api/setup/test_screenshot")
async def setup_test_screenshot():
    try:
        r = subprocess.run(
            ["screencapture", "-x", "/tmp/sompter_setup_test.png"],
            capture_output=True, timeout=10,
        )
        if r.returncode == 0:
            size = os.path.getsize("/tmp/sompter_setup_test.png")
            os.unlink("/tmp/sompter_setup_test.png")
            return {"success": True, "message": f"Screenshot captured ({size} bytes)"}
        error_msg = (r.stderr or b"").decode().strip() or "unknown error"
        return {"success": False, "message": f"screencapture failed: {error_msg[:200]}"}
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "Screenshot timed out — grant Screen Recording permission"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/setup/test_control")
async def setup_test_control():
    try:
        x, y = pyautogui.position()
        return {"success": True, "message": f"Mouse position: ({x}, {y})", "mouse_x": x, "mouse_y": y}
    except Exception as e:
        return {"success": False, "message": f"Cannot read mouse: {e}"}


@app.post("/api/setup/test_opencode")
async def setup_test_opencode():
    port = find_opencode_server()
    if port:
        return {"success": True, "message": f"OpenCode serve running on port {port}"}
    return {"success": False, "message": "OpenCode serve not running (fallback prompt saving available)"}


@app.post("/api/search")
async def search(query: str):
    return {"results": web_search(query)}


@app.post("/api/action/click")
async def action_click(req: ClickRequest):
    try:
        pyautogui.click(req.x, req.y)
        return {"success": True, "message": f"Clicked at ({req.x}, {req.y})"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/action/type")
async def action_type(req: TypeRequest):
    try:
        pyautogui.typewrite(req.text, interval=0.05)
        return {"success": True, "message": f"Typed: {req.text[:50]}{'...' if len(req.text) > 50 else ''}"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/action/hotkey")
async def action_hotkey(req: HotkeyRequest):
    try:
        pyautogui.hotkey(*req.keys)
        return {"success": True, "message": f"Pressed: {'+'.join(req.keys)}"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/action/open_app")
async def action_open_app(req: OpenAppRequest):
    try:
        subprocess.run(["open", "-a", req.name], check=True, timeout=15)
        return {"success": True, "message": f"Opened: {req.name}"}
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "Timeout opening app"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/action/run_command")
async def action_run_command(req: RunCommandRequest):
    safe, reason = check_command_safety(req.command)
    if not safe:
        return {"success": False, "message": reason}

    try:
        result = subprocess.run(
            req.command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout.strip() or result.stderr.strip() or "(no output)"
        return {"success": True, "message": output[:500]}
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "Command timed out (30s)"}
    except Exception as e:
        return {"success": False, "message": str(e)}


SUSPICIOUS_PATH_COMPONENTS = [
    "/etc/", "/var/", "/System/",
    "/bin/", "/sbin/", "/dev/",
    "/private/", "/tmp/",
]

OPCODE_SERVE_PORTS = [4096, 4097, 4098, 4099, 4100]


OPCODE_SERVER_PASSWORD = os.environ.get("OPENCODE_SERVER_PASSWORD") or os.environ.get("OPCODE_SERVER_PASSWORD") or ""


def find_opencode_server() -> int | None:
    for port in OPCODE_SERVE_PORTS:
        try:
            auth = ("opencode", OPCODE_SERVER_PASSWORD) if OPCODE_SERVER_PASSWORD else None
            r = requests.get(f"http://localhost:{port}/global/health", auth=auth, timeout=1)
            if r.status_code == 200:
                return port
        except Exception:
            continue
    return None


def parse_opencode_json_output(stdout: str) -> str:
    parts = []
    for line in stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            parts.append(line)
            continue
        if event.get("type") == "text":
            text = event.get("part", {}).get("text", "")
            if text:
                parts.append(text)
    return "\n".join(parts)


SESSION_NOT_FOUND_PATTERNS = [
    "session not found",
    "no session",
    "attach failed",
    "connection refused",
]


class SessionNotFoundError(Exception):
    pass


def run_opencode_attach(project_path: str, prompt: str) -> dict:
    port = find_opencode_server()
    if port is None:
        raise SessionNotFoundError("No opencode serve instance running on ports 4096-4100.")

    cmd = ["opencode", "run", prompt, "--attach", f"http://localhost:{port}", "--format", "json"]
    if OPCODE_SERVER_PASSWORD:
        cmd += ["--password", OPCODE_SERVER_PASSWORD]

    result = subprocess.run(
        cmd,
        cwd=project_path,
        capture_output=True,
        text=True,
        timeout=180,
    )
    raw = (result.stdout or "") + (result.stderr or "")
    output = parse_opencode_json_output(result.stdout)
    if not output:
        output = raw.strip()
    if not output:
        output = "(no output from OpenCode)"

    # Check for session-not-found patterns
    if any(p in (output + raw).lower() for p in SESSION_NOT_FOUND_PATTERNS):
        raise SessionNotFoundError(output[:500])

    return {"success": True, "output": output[:2000], "fallback": False}


def save_opencode_prompt(project_path: str, prompt: str, raw_error: str | None = None) -> dict:
    sompter_dir = os.path.join(project_path, ".sompter")
    os.makedirs(sompter_dir, exist_ok=True)
    prompt_file = os.path.join(sompter_dir, "opencode-prompt.txt")
    with open(prompt_file, "w") as f:
        f.write(prompt)
    return {
        "success": True,
        "fallback_saved": True,
        "output": "OpenCode session not active. I saved the prompt here: .sompter/opencode-prompt.txt. Open OpenCode in this project and paste/run that prompt manually.",
        "prompt_file": prompt_file,
        "raw_error": raw_error,
    }


@app.post("/api/opencode/run")
async def opencode_run(req: OpenCodeRunRequest):
    project_path = os.path.abspath(os.path.expanduser(req.project_path))

    if not os.path.isdir(project_path):
        return {"success": False, "output": f"Project path does not exist or is not a directory: {req.project_path}"}

    home = os.path.expanduser("~")
    if not project_path.startswith(home):
        return {"success": False, "output": "Project path must be inside your home directory for safety."}

    for comp in SUSPICIOUS_PATH_COMPONENTS:
        if project_path.startswith(comp):
            return {"success": False, "output": f"Blocked: suspicious path component '{comp}'"}

    opencode_prompt = (
        f"You are working inside this local project. The user wants: {req.task}\n"
        "Find the issue, explain it shortly, make safe code changes only, and run tests if available.\n"
        "Do not delete files. Do not use sudo. Do not touch .env/auth/API keys.\n"
        "If a risky command is needed, stop and explain."
    )

    if req.screenshot_base64:
        try:
            desc = call_ai(
                req.screenshot_base64,
                "Describe what's on this screenshot in 2-3 sentences. Focus on errors, code, or tasks visible.",
                "You are a screenshot describer.",
            )
            opencode_prompt += f"\n\nScreenshot context: {desc}"
        except Exception:
            pass

    run_id = create_run_snapshot(project_path, "opencode", opencode_prompt)

    def finish(result: dict, status: str = "completed") -> dict:
        output = result.get("output", "") or result.get("message", "")
        finish_run_snapshot(project_path, run_id, output[:2000], status)
        result["run_id"] = run_id
        return result

    # Strategy 1: attach to a running opencode serve instance
    try:
        result = run_opencode_attach(project_path, opencode_prompt)
        result["run_id"] = run_id
        output = result.get("output", "")
        finish_run_snapshot(project_path, run_id, output[:2000], "completed")
        return result
    except SessionNotFoundError as e:
        return finish(save_opencode_prompt(project_path, opencode_prompt, raw_error=str(e)), "fallback")
    except FileNotFoundError:
        return finish(save_opencode_prompt(project_path, opencode_prompt), "fallback")
    except subprocess.TimeoutExpired:
        return finish(save_opencode_prompt(project_path, opencode_prompt, raw_error="OpenCode attach timed out (180s)."), "fallback")
    except Exception:
        pass

    # Strategy 2: run opencode directly
    try:
        result = subprocess.run(
            ["opencode", "run", opencode_prompt],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=180,
        )
        output = (result.stdout or result.stderr or "").strip()
        if not output:
            output = "(no output from OpenCode)"

        if result.returncode != 0:
            return finish(save_opencode_prompt(project_path, opencode_prompt), "error")

        response = {"success": True, "output": output[:2000], "fallback": False, "run_id": run_id}
        finish_run_snapshot(project_path, run_id, output[:2000], "completed")
        return response
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return finish(save_opencode_prompt(project_path, opencode_prompt), "fallback")
    except Exception as e:
        return {"success": False, "output": f"Error running OpenCode: {str(e)}", "fallback": False, "run_id": run_id}


def validate_project_path(project_path: str) -> str | None:
    path = os.path.abspath(os.path.expanduser(project_path))
    if not os.path.isdir(path):
        return f"Project path does not exist or is not a directory: {project_path}"
    home = os.path.expanduser("~")
    if not path.startswith(home):
        return "Project path must be inside your home directory for safety."
    for comp in SUSPICIOUS_PATH_COMPONENTS:
        if path.startswith(comp):
            return f"Blocked: suspicious path component '{comp}'"
    return None


@app.get("/api/project/status")
async def project_status(project_path: str):
    err = validate_project_path(project_path)
    if err:
        return {"success": False, "message": err}
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=15,
        )
        output = result.stdout.strip() or "(no changes)"
        return {"success": True, "files": output}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/api/project/diff")
async def project_diff(project_path: str):
    err = validate_project_path(project_path)
    if err:
        return {"success": False, "message": err}
    try:
        stat_result = subprocess.run(
            ["git", "diff", "--stat"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=15,
        )
        diff_result = subprocess.run(
            ["git", "diff", "--color=never"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=15,
        )
        diff_stat = stat_result.stdout.strip() or "(no changes)"
        diff_full = diff_result.stdout.strip() or "(no diff)"
        return {"success": True, "stat": diff_stat, "diff": diff_full[:5000]}
    except Exception as e:
        return {"success": False, "message": str(e)}


ALLOWED_TEST_PREFIXES = [
    "npm test", "npm run test", "npm run build",
    "pytest", "python -m pytest",
]


@app.post("/api/project/test")
async def project_test(req: TestRunRequest):
    err = validate_project_path(req.project_path)
    if err:
        return {"success": False, "message": err}

    cmd = req.command.strip()
    allowed = any(cmd.startswith(p) for p in ALLOWED_TEST_PREFIXES)
    if not allowed:
        return {"success": False, "message": f"Command not allowed. Must start with one of: {', '.join(ALLOWED_TEST_PREFIXES)}"}

    cmd_lower = cmd.lower()
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, cmd_lower):
            return {"success": False, "message": f"Blocked: command contains '{pattern}'"}

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=req.project_path,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = (result.stdout or result.stderr or "").strip()
        if not output:
            output = "(no output)"
        return {"success": result.returncode == 0, "message": output[:2000], "exit_code": result.returncode}
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "Test command timed out (120s)"}
    except Exception as e:
        return {"success": False, "message": str(e)}


SMARTFIX_SYSTEM_PROMPT = (
    "You are a senior developer debugging a visible screen issue. "
    "Look at the screenshot carefully. Identify errors, crashes, visual bugs, or tasks on screen. "
    "Be specific: name the app, the error message, the line number, or the UI element. "
    "Respond in 2-4 sentences maximum."
)


@app.post("/api/smartfix/run")
async def smartfix_run(req: SmartFixRequest):
    project_path = os.path.abspath(os.path.expanduser(req.project_path))
    err = validate_project_path_fast(project_path)
    if err:
        return {"success": False, "screen_summary": "", "opencode_result": err}

    screen_summary = ""
    try:
        screen_summary = call_ai(
            req.screenshot_base64,
            "Describe exactly what's on this screen in 2-4 sentences. Focus on errors, crashes, or tasks visible.",
            SMARTFIX_SYSTEM_PROMPT,
        )
    except Exception as e:
        screen_summary = f"(Screenshot analysis unavailable: {e})"

    lines = [
        f"You are fixing this local project: {req.project_name or os.path.basename(project_path)}",
        f"Path: {project_path}",
        "",
        "Visible screen problem:",
        screen_summary,
    ]
    if req.user_prompt:
        lines += ["", "User instruction:", req.user_prompt]

    lines += [
        "",
        "Task:",
        "Find the likely source of the issue, make safe code changes only, and run safe checks if available.",
        "Do not delete files. Do not use sudo.",
        "Do not touch .env, auth.json, passwords, API keys, or secrets.",
        "Do not install packages unless clearly necessary; if needed, explain instead of running.",
        "After changes, summarize what changed and what to test.",
    ]
    opencode_prompt = "\n".join(lines)

    run_id = create_run_snapshot(project_path, "smartfix", opencode_prompt, screen_summary)

    try:
        oc_result = run_opencode_attach(project_path, opencode_prompt)
        output = oc_result.get("output", "") or oc_result.get("message", "")
        finish_run_snapshot(project_path, run_id, output[:2000], "completed")
        return {
            "success": oc_result.get("success", False),
            "run_id": run_id,
            "screen_summary": screen_summary,
            "opencode_result": oc_result,
            "fallback": oc_result.get("fallback_saved", False),
        }
    except SessionNotFoundError as e:
        fallback = save_opencode_prompt(project_path, opencode_prompt, raw_error=str(e))
        finish_run_snapshot(project_path, run_id, str(fallback), "fallback")
        return {
            "success": True,
            "run_id": run_id,
            "screen_summary": screen_summary,
            "opencode_result": fallback,
            "fallback": True,
        }
    except FileNotFoundError:
        fallback = save_opencode_prompt(project_path, opencode_prompt)
        finish_run_snapshot(project_path, run_id, str(fallback), "fallback")
        return {
            "success": True,
            "run_id": run_id,
            "screen_summary": screen_summary,
            "opencode_result": fallback,
            "fallback": True,
        }
    except subprocess.TimeoutExpired:
        fallback = save_opencode_prompt(project_path, opencode_prompt, raw_error="OpenCode timed out (180s).")
        finish_run_snapshot(project_path, run_id, str(fallback), "fallback")
        return {
            "success": True,
            "run_id": run_id,
            "screen_summary": screen_summary,
            "opencode_result": fallback,
            "fallback": True,
        }
    except Exception as e:
        finish_run_snapshot(project_path, run_id, str(e), "error")
        return {
            "success": False,
            "run_id": run_id,
            "screen_summary": screen_summary,
            "opencode_result": {"success": False, "output": f"Smart Fix error: {str(e)}"},
            "fallback": False,
        }


def validate_project_path_fast(path: str) -> str | None:
    if not os.path.isdir(path):
        return f"Project path does not exist or is not a directory: {path}"
    home = os.path.expanduser("~")
    if not path.startswith(home):
        return "Project path must be inside your home directory for safety."
    for comp in SUSPICIOUS_PATH_COMPONENTS:
        if path.startswith(comp):
            return f"Blocked: suspicious path component '{comp}'"
    return None


# ---- Provider Settings ----

SOMPTER_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".sompter")
SETTINGS_PATH = os.path.join(SOMPTER_DIR, "settings.json")
DOTENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")

DEFAULT_SETTINGS = {
    "mode": "auto",
    "ollama_model": "gemma3:12b",
    "gemini_model": "gemini-2.0-flash",
    "openai_model": "gpt-4o-mini",
}


def load_settings() -> dict:
    if os.path.isfile(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH) as f:
                return {**DEFAULT_SETTINGS, **json.load(f)}
        except Exception:
            pass
    return dict(DEFAULT_SETTINGS)


def save_settings(data: dict):
    os.makedirs(SOMPTER_DIR, exist_ok=True)
    existing = load_settings()
    existing.update(data)
    with open(SETTINGS_PATH, "w") as f:
        json.dump(existing, f, indent=2)


def refresh_env():
    load_dotenv(override=True)
    global ollama_model, gemini_api_key, gemini_api_key_raw, gemini_model, openai_api_key, openai_api_key_raw, openai_model
    ollama_model = os.getenv("OLLAMA_MODEL", "gemma3:12b")
    gemini_api_key_raw = os.getenv("GEMINI_API_KEY", "")
    gemini_api_key = gemini_api_key_raw if gemini_api_key_raw and gemini_api_key_raw != "put_your_gemini_api_key_here" else ""
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    openai_api_key_raw = os.getenv("OPENAI_API_KEY", "")
    openai_api_key = openai_api_key_raw if openai_api_key_raw and openai_api_key_raw != "put_your_openai_api_key_here" else ""
    openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def mask_key(key: str) -> str:
    if not key or len(key) < 8:
        return key
    return key[:4] + "..." + key[-4:]


def write_env_key(key_name: str, value: str) -> tuple[bool, str]:
    try:
        lines = []
        found = False
        if os.path.isfile(DOTENV_PATH):
            with open(DOTENV_PATH) as f:
                lines = f.readlines()

        with open(DOTENV_PATH, "w") as f:
            for line in lines:
                stripped = line.strip()
                if stripped.startswith(key_name + "=") or stripped.startswith("# " + key_name + "="):
                    continue
                if stripped.startswith("#") or stripped == "":
                    f.write(line)
                    continue
                f.write(line)
            f.write(f'{key_name}="{value}"\n')
        return True, "Saved"
    except Exception as e:
        return False, str(e)


@app.get("/api/settings")
async def settings_get():
    s = load_settings()
    refresh_env()
    return {
        "mode": s.get("mode", "auto"),
        "ollama_model": s.get("ollama_model", ollama_model),
        "gemini_model": s.get("gemini_model", gemini_model),
        "openai_model": s.get("openai_model", openai_model),
        "ollama_available": ollama_available(),
        "gemini_available": bool(gemini_api_key),
        "openai_available": bool(openai_api_key),
        "gemini_key_masked": mask_key(gemini_api_key) if gemini_api_key else "",
        "openai_key_masked": mask_key(openai_api_key) if openai_api_key else "",
        "active_provider": "ollama" if ollama_available() else "gemini" if gemini_api_key else "openai" if openai_api_key else "none",
    }


class SettingsSaveRequest(BaseModel):
    mode: str = "auto"
    ollama_model: str | None = None
    gemini_model: str | None = None
    openai_model: str | None = None
    gemini_key: str | None = None
    openai_key: str | None = None


@app.post("/api/settings")
async def settings_save(req: SettingsSaveRequest):
    try:
        non_secret = {}
        if req.mode in ("auto", "ollama", "gemini", "openai"):
            non_secret["mode"] = req.mode
        if req.ollama_model:
            non_secret["ollama_model"] = req.ollama_model
        if req.gemini_model:
            non_secret["gemini_model"] = req.gemini_model
        if req.openai_model:
            non_secret["openai_model"] = req.openai_model
        save_settings(non_secret)

        if req.gemini_key:
            ok, msg = write_env_key("GEMINI_API_KEY", req.gemini_key)
            if not ok:
                return {"success": False, "message": f"Failed to save Gemini key: {msg}"}
        if req.openai_key:
            ok, msg = write_env_key("OPENAI_API_KEY", req.openai_key)
            if not ok:
                return {"success": False, "message": f"Failed to save OpenAI key: {msg}"}

        refresh_env()
        return {"success": True, "message": "Settings saved"}
    except Exception as e:
        return {"success": False, "message": str(e)}


class TestProviderRequest(BaseModel):
    provider: str


@app.post("/api/settings/test_provider")
async def settings_test_provider(req: TestProviderRequest):
    refresh_env()
    try:
        if req.provider == "ollama":
            if ollama_available():
                resp = requests.post(
                    "http://localhost:11434/api/generate",
                    json={"model": ollama_model, "prompt": "say ok", "stream": False},
                    timeout=30,
                )
                if resp.status_code == 200:
                    return {"success": True, "message": f"Ollama {ollama_model} responds OK"}
                return {"success": False, "message": f"Ollama returned status {resp.status_code}"}
            return {"success": False, "message": "Ollama is not running (port 11434)"}
        elif req.provider == "gemini":
            if not gemini_api_key:
                return {"success": False, "message": "No Gemini API key set"}
            import google.genai as genai
            client = genai.Client(api_key=gemini_api_key)
            resp = client.models.generate_content(model=gemini_model, contents="say ok")
            if resp and resp.text:
                return {"success": True, "message": f"Gemini {gemini_model} responds OK"}
            return {"success": False, "message": "Gemini returned empty response"}
        elif req.provider == "openai":
            if not openai_api_key:
                return {"success": False, "message": "No OpenAI API key set"}
            from openai import OpenAI
            client = OpenAI(api_key=openai_api_key)
            resp = client.chat.completions.create(
                model=openai_model, messages=[{"role": "user", "content": "say ok"}]
            )
            if resp and resp.choices:
                return {"success": True, "message": f"OpenAI {openai_model} responds OK"}
            return {"success": False, "message": "OpenAI returned empty response"}
        return {"success": False, "message": f"Unknown provider: {req.provider}"}
    except Exception as e:
        return {"success": False, "message": f"{req.provider} test failed: {str(e)}"}

SECRET_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
    re.compile(r"bearer\s+[a-zA-Z0-9\-_\.]{20,}", re.IGNORECASE),
    re.compile(r"(api[_-]?key|apikey|secret|password|token)\s*[:=]\s*['\"]?[a-zA-Z0-9\-_\.]{8,}['\"]?", re.IGNORECASE),
]


def mask_value(val: str) -> str:
    for pat in SECRET_PATTERNS:
        val = pat.sub(lambda m: m.group(0)[:4] + "..." + m.group(0)[-4:] if len(m.group(0)) > 12 else m.group(0)[:4] + "...", val)
    return val


def mask_dict(obj):
    if isinstance(obj, dict):
        return {k: mask_dict(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [mask_dict(v) for v in obj]
    elif isinstance(obj, str):
        return mask_value(obj)
    return obj


@app.get("/api/diagnostics")
async def diagnostics(project_path: str | None = None):
    data = {
        "timestamp": datetime.datetime.now().isoformat(),
        "app": {},
        "system": {},
        "services": {},
        "project": None,
        "snapshots": [],
    }

    pkg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "package.json")
    if os.path.isfile(pkg_path):
        try:
            with open(pkg_path) as f:
                pkg = json.load(f)
            data["app"]["version"] = pkg.get("version", "unknown")
            data["app"]["name"] = pkg.get("name", "unknown")
        except Exception:
            data["app"]["version"] = "unknown"
    else:
        data["app"]["version"] = "unknown"

    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=repo_dir, capture_output=True, text=True, timeout=5)
        data["app"]["git_commit"] = r.stdout.strip() if r.returncode == 0 else "unknown"
    except Exception:
        data["app"]["git_commit"] = "unknown"

    data["system"]["python"] = subprocess.run(["python3", "--version"], capture_output=True, text=True, timeout=5).stdout.strip() or "unknown"
    try:
        r = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=5)
        data["system"]["node"] = r.stdout.strip() if r.returncode == 0 else "not found"
    except Exception:
        data["system"]["node"] = "not found"
    try:
        r = subprocess.run(["npm", "--version"], capture_output=True, text=True, timeout=5)
        data["system"]["npm"] = r.stdout.strip() if r.returncode == 0 else "not found"
    except Exception:
        data["system"]["npm"] = "not found"

    for port in [8787, 4096, 11434]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            r = s.connect_ex(("127.0.0.1", port))
            data["services"][f"port_{port}"] = "open" if r == 0 else "closed"
            s.close()
        except Exception:
            data["services"][f"port_{port}"] = "error"

    if project_path:
        pp = os.path.abspath(os.path.expanduser(project_path))
        err = validate_project_path_fast(pp)
        if err:
            data["project"] = {"error": err}
        else:
            data["project"] = {
                "path": pp,
                "name": os.path.basename(pp),
                "dir_exists": os.path.isdir(pp),
            }
            snapshots = list_run_snapshots(pp)
            data["snapshots"] = [s.get("run_id", "") for s in snapshots[:5]]

    data = mask_dict(data)
    return data

RUNS_DIR_NAME = ".sompter/runs"
SUSPICIOUS_RUN_FILES = [".env", "auth.json", "password", "api_key"]


def get_runs_dir(project_path: str) -> str:
    return os.path.join(project_path, RUNS_DIR_NAME)


def create_run_id() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]


def save_run_file(run_dir: str, name: str, content: str):
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, name), "w") as f:
        f.write(content)


def read_run_file(run_dir: str, name: str) -> str | None:
    fpath = os.path.join(run_dir, name)
    if os.path.exists(fpath):
        with open(fpath) as f:
            return f.read()
    return None


def run_git_safe(project_path: str, args: list[str], timeout: int = 15) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["git"] + args, cwd=project_path, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None


def create_run_snapshot(project_path: str, mode: str, task: str, screen_summary: str = "") -> str:
    run_id = create_run_id()
    snapshot_dir = os.path.join(get_runs_dir(project_path), run_id)
    os.makedirs(snapshot_dir, exist_ok=True)

    meta = {
        "run_id": run_id,
        "mode": mode,
        "timestamp": datetime.datetime.now().isoformat(),
        "task": task[:200],
        "screen_summary": screen_summary[:200],
        "status": "running",
    }
    save_run_file(snapshot_dir, "meta.json", json.dumps(meta, indent=2))
    save_run_file(snapshot_dir, "task.txt", task)

    status = run_git_safe(project_path, ["status", "--short"])
    save_run_file(snapshot_dir, "before-status.txt", status.stdout if status else "(no git)")

    diff = run_git_safe(project_path, ["diff", "--color=never"])
    save_run_file(snapshot_dir, "before-diff.patch", diff.stdout if diff else "(no git or no diff)")

    return run_id


def finish_run_snapshot(project_path: str, run_id: str, opencode_output: str, status: str = "completed"):
    snapshot_dir = os.path.join(get_runs_dir(project_path), run_id)
    if not os.path.isdir(snapshot_dir):
        return

    meta_path = os.path.join(snapshot_dir, "meta.json")
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)
        meta["end_timestamp"] = datetime.datetime.now().isoformat()
        meta["status"] = status
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)

    save_run_file(snapshot_dir, "opencode-output.txt", opencode_output)

    status_res = run_git_safe(project_path, ["status", "--short"])
    save_run_file(snapshot_dir, "after-status.txt", status_res.stdout if status_res else "(no git)")

    diff = run_git_safe(project_path, ["diff", "--color=never"])
    save_run_file(snapshot_dir, "after-diff.patch", diff.stdout if diff else "(no git or no diff)")


def list_run_snapshots(project_path: str) -> list[dict]:
    runs_dir = get_runs_dir(project_path)
    if not os.path.isdir(runs_dir):
        return []

    runs = []
    for name in sorted(os.listdir(runs_dir), reverse=True):
        meta_path = os.path.join(runs_dir, name, "meta.json")
        if os.path.exists(meta_path):
            with open(meta_path) as f:
                runs.append(json.load(f))
            if len(runs) >= 10:
                break
    return runs


def get_run_detail(project_path: str, run_id: str) -> dict | None:
    snapshot_dir = os.path.join(get_runs_dir(project_path), run_id)
    if not os.path.isdir(snapshot_dir):
        return None

    detail = {}
    for fname in ["meta.json", "task.txt", "before-status.txt", "before-diff.patch",
                   "opencode-output.txt", "after-status.txt", "after-diff.patch"]:
        content = read_run_file(snapshot_dir, fname)
        if content is not None:
            key = fname.replace(".", "_").replace("-", "_")
            detail[key] = content[:5000] if len(content) > 5000 else content
    return detail


@app.get("/api/runs/list")
async def runs_list(project_path: str):
    err = validate_project_path_fast(os.path.abspath(os.path.expanduser(project_path)))
    if err:
        return {"success": False, "runs": [], "message": err}
    return {"success": True, "runs": list_run_snapshots(project_path)}


@app.get("/api/runs/detail")
async def runs_detail(project_path: str, run_id: str):
    err = validate_project_path_fast(os.path.abspath(os.path.expanduser(project_path)))
    if err:
        return {"success": False, "message": err}
    detail = get_run_detail(project_path, run_id)
    if not detail:
        return {"success": False, "message": "Run not found"}
    return {"success": True, "detail": detail}


class UndoRequest(BaseModel):
    project_path: str
    run_id: str


@app.post("/api/runs/undo")
async def runs_undo(req: UndoRequest):
    project_path = os.path.abspath(os.path.expanduser(req.project_path))
    err = validate_project_path_fast(project_path)
    if err:
        return {"success": False, "message": err}

    snapshot_dir = os.path.join(get_runs_dir(project_path), req.run_id)
    if not os.path.isdir(snapshot_dir):
        return {"success": False, "message": "Run snapshot not found"}

    before_diff = read_run_file(snapshot_dir, "before-diff.patch")
    if not before_diff or before_diff.strip() in ("", "(no git or no diff)", "(no changes)"):
        return {"success": False, "message": "No changes to undo"}

    for sf in SUSPICIOUS_RUN_FILES:
        if sf in before_diff.lower():
            return {"success": False, "message": f"Undo blocked: snapshot touches '{sf}' files. Review with Show Diff."}

    try:
        result = subprocess.run(
            ["git", "apply", "--reverse", os.path.join(snapshot_dir, "before-diff.patch")],
            cwd=project_path, capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return {"success": True, "message": "Changes reverted. Review with Show Diff."}
        return {"success": False, "message": f"Undo failed: {result.stderr[:300]}"}
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "Undo timed out"}
    except Exception as e:
        return {"success": False, "message": str(e)}
