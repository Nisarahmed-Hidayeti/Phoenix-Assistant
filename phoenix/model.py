"""Loopback llama.cpp chat-completions adapter."""
import http.client
import ipaddress
import json
import os
import re
import socket
import threading
import urllib.parse


class ModelError(RuntimeError):
    pass


class Llama:
    def __init__(self, endpoint=None):
        url = urllib.parse.urlsplit(endpoint or os.getenv("PHOENIX_SERVER", "http://127.0.0.1:8080"))
        try:
            local = ipaddress.ip_address(url.hostname or "").is_loopback
        except ValueError:
            local = url.hostname == "localhost"
        if not local or url.scheme != "http" or url.username is not None or url.path not in ("", "/") or url.query or url.fragment:
            raise ValueError("Use a loopback HTTP server URL such as http://127.0.0.1:8080.")
        self.host = "127.0.0.1" if url.hostname == "localhost" else url.hostname
        self.port = 80 if url.port is None else url.port
        if not self.port:
            raise ValueError("Set a valid loopback server port.")
        self.lock = threading.Lock()
        self.api_key = os.getenv("PHOENIX_API_KEY", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", self.api_key):
            raise ValueError("Set PHOENIX_API_KEY to the private token used to start llama.cpp.")

    def chat(self, messages, tools, timeout):
        payload = dict(model="local", messages=messages, tools=tools,
                       parallel_tool_calls=False, temperature=0, max_tokens=400,
                       chat_template_kwargs={"enable_thinking": False})
        with self.lock:
            connection = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
            try:
                headers = {"Content-Type": "application/json"}
                headers["Authorization"] = f"Bearer {self.api_key}"
                connection.request("POST", "/v1/chat/completions", json.dumps(payload), headers)
                response = connection.getresponse()
                if response.status == 401:
                    raise ModelError("The local server rejected PHOENIX_API_KEY.")
                if response.status == 503:
                    raise ModelError("The local model is loading or unavailable. Check the GGUF path and server log.")
                if response.status != 200:
                    raise ModelError(f"llama.cpp returned HTTP {response.status}. Check its template, context and server log.")
                body = response.read(256 * 1024 + 1)
                if len(body) > 256 * 1024:
                    raise ModelError("The local model response exceeded the size limit.")
                result = json.loads(body)["choices"][0]["message"]
                if not isinstance(result, dict):
                    raise TypeError
                return result
            except (TimeoutError, socket.timeout) as exc:
                raise ModelError("The local model timed out.") from exc
            except (OSError, http.client.HTTPException) as exc:
                raise ModelError("Cannot reach llama.cpp. Check the loopback port and whether the GGUF loaded.") from exc
            except (ValueError, KeyError, IndexError, TypeError, RecursionError) as exc:
                raise ModelError("llama.cpp returned malformed chat output.") from exc
            finally:
                connection.close()
