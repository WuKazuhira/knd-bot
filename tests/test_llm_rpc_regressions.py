import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import aiorpcx

from plugins.llm.api_provider import (
    ApiProvider,
    LlmModel,
    _futureppo_gemini_url,
    _gemini_request_body,
    _normalize_gemini_response,
)
from services.autochat.serve import RpcSession


class GeminiAdapterTests(unittest.IsolatedAsyncioTestCase):
    def test_builds_native_generate_content_url(self):
        self.assertEqual(
            _futureppo_gemini_url(
                "https://api.futureppo.top/v1",
                "gemini-3.5-flash-lite",
            ),
            "https://api.futureppo.top/v1beta/models/gemini-3.5-flash-lite:generateContent",
        )

    def test_converts_openai_messages_to_gemini_body(self):
        body = _gemini_request_body(
            [
                {"role": "system", "content": "保持简洁"},
                {"role": "user", "content": "你好"},
                {"role": "user", "content": "继续"},
                {"role": "assistant", "content": "你好，有什么需要？"},
            ],
            128,
            {"generationConfig": {"temperature": 0.2}},
            {"generationConfig": {"topP": 0.8}},
        )
        self.assertEqual(body["systemInstruction"], {"parts": [{"text": "保持简洁"}]})
        self.assertEqual(body["contents"][0]["role"], "user")
        self.assertEqual(
            body["contents"][0]["parts"],
            [{"text": "你好"}, {"text": "继续"}],
        )
        self.assertEqual(body["contents"][1]["role"], "model")
        self.assertEqual(body["generationConfig"]["maxOutputTokens"], 128)
        self.assertEqual(body["generationConfig"]["temperature"], 0.2)
        self.assertEqual(body["generationConfig"]["topP"], 0.8)

    def test_normalizes_native_response(self):
        result = _normalize_gemini_response(
            {
                "candidates": [{
                    "content": {
                        "parts": [
                            {"text": "思考", "thought": True},
                            {"text": "答案"},
                        ]
                    },
                    "finishReason": "STOP",
                }],
                "usageMetadata": {
                    "promptTokenCount": 11,
                    "candidatesTokenCount": 7,
                    "totalTokenCount": 18,
                },
            }
        )
        message = result["choices"][0]["message"]
        self.assertEqual(message["content"], "答案")
        self.assertEqual(message["reasoning_content"], "思考")
        self.assertEqual(result["usage"]["prompt_tokens"], 11)
        self.assertEqual(result["usage"]["completion_tokens"], 7)

    async def test_futureppo_gemini_forces_proxy_and_native_endpoint(self):
        provider = ApiProvider("futureppo", "fh")
        model = LlmModel(name="gemini-3.5-flash-lite", model_id="gemini-3.5-flash-lite")
        response = {
            "candidates": [{"content": {"parts": [{"text": "ok"}]}}]
        }
        with patch(
            "plugins.llm.api_provider._request_bytes",
            new=AsyncMock(return_value=json.dumps(response).encode()),
        ) as request:
            result = await provider.chat_completions(
                model,
                [{"role": "user", "content": "ping"}],
                max_tokens=64,
                timeout=30,
            )
        request.assert_awaited_once()
        self.assertEqual(
            request.await_args.args[1],
            "https://api.futureppo.top/v1beta/models/gemini-3.5-flash-lite:generateContent",
        )
        self.assertTrue(request.await_args.kwargs["force_proxy"])
        self.assertEqual(request.await_args.kwargs["json_body"]["contents"][0]["parts"], [{"text": "ping"}])
        self.assertEqual(result["choices"][0]["message"]["content"], "ok")


class _FakeWebsocket:
    def __init__(self):
        self.exit_count = 0

    async def __aexit__(self, *args):
        self.exit_count += 1


class _FakeRpcSession:
    def __init__(self, error):
        self.error = error

    async def send_request(self, *args):
        raise self.error


class RpcDisconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_business_rpc_error_keeps_connection(self):
        rpc = RpcSession("127.0.0.1", 8765, "token", 5)
        websocket = _FakeWebsocket()
        rpc.session = _FakeRpcSession(aiorpcx.RPCError(-32603, "internal server error"))
        rpc.ws_client = websocket

        with self.assertRaises(aiorpcx.RPCError):
            await rpc.call("query_llm")

        self.assertIsNotNone(rpc.session)
        self.assertEqual(websocket.exit_count, 0)

    async def test_transport_error_disconnects_connection(self):
        rpc = RpcSession("127.0.0.1", 8765, "token", 5)
        websocket = _FakeWebsocket()
        rpc.session = _FakeRpcSession(ConnectionResetError("closed"))
        rpc.ws_client = websocket

        with self.assertRaises(ConnectionResetError):
            await rpc.call("get_new_msgs")

        self.assertIsNone(rpc.session)
        self.assertIsNone(rpc.ws_client)
        self.assertEqual(websocket.exit_count, 1)


if __name__ == "__main__":
    unittest.main()
