import os
import base64
import json
import re
import subprocess
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
    return {
        "ollama": {"available": ollama_available(), "model": ollama_model},
        "gemini": {"available": bool(gemini_api_key), "model": gemini_model},
        "openai": {"available": bool(openai_api_key), "model": openai_model},
    }


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

    # Strategy 1: attach to a running opencode serve instance
    try:
        return run_opencode_attach(project_path, opencode_prompt)
    except SessionNotFoundError as e:
        return save_opencode_prompt(project_path, opencode_prompt, raw_error=str(e))
    except FileNotFoundError:
        return save_opencode_prompt(project_path, opencode_prompt)
    except subprocess.TimeoutExpired:
        return save_opencode_prompt(project_path, opencode_prompt, raw_error="OpenCode attach timed out (180s).")
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
            return save_opencode_prompt(project_path, opencode_prompt)

        return {"success": True, "output": output[:2000], "fallback": False}
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return save_opencode_prompt(project_path, opencode_prompt)
    except Exception as e:
        return {"success": False, "output": f"Error running OpenCode: {str(e)}", "fallback": False}


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
