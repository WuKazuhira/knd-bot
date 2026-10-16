import base64
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from services.pjsk_draw import serve


class DrawHTTPContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        transport = httpx.ASGITransport(app=serve.app, raise_app_exceptions=False)
        self.client = httpx.AsyncClient(transport=transport, base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_single_image_returns_raw_bytes_and_detected_mime(self):
        image = bytes.fromhex('89504e470d0a1a0a') + b'image-bytes'
        with patch.object(serve, 'dispatch', new_callable=AsyncMock) as dispatch:
            dispatch.return_value = ([image], {})
            response = await self.client.post('/render/song_info', json={'music_id': 39})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['content-type'], 'image/png')
        self.assertEqual(response.content, image)
        dispatch.assert_awaited_once_with('song_info', {'music_id': 39})

    async def test_multiple_images_and_meta_use_ordered_base64_envelope(self):
        images = [b'first-image', b'second-image']
        meta = {'pages': 2}
        with patch.object(serve, 'dispatch', new_callable=AsyncMock) as dispatch:
            dispatch.return_value = (images, meta)
            response = await self.client.post('/render/song_quiz', json={})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['content-type'], 'application/json')
        self.assertEqual(
            response.json(),
            {
                'images': [base64.b64encode(image).decode() for image in images],
                'meta': meta,
            },
        )

    async def test_non_object_payload_returns_400_without_dispatch(self):
        with patch.object(serve, 'dispatch', new_callable=AsyncMock) as dispatch:
            response = await self.client.post('/render/song_info', json=['invalid'])

        self.assertEqual(response.status_code, 400)
        self.assertIn('JSON 对象', response.json()['detail'])
        dispatch.assert_not_awaited()

    async def test_unknown_renderer_returns_404(self):
        with patch.object(serve, 'dispatch', new_callable=AsyncMock) as dispatch:
            dispatch.side_effect = KeyError('unknown renderer')
            response = await self.client.post('/render/missing_task', json={})

        self.assertEqual(response.status_code, 404)
        self.assertIn('unknown renderer', response.json()['detail'])

    async def test_renderer_exception_returns_500(self):
        with patch.object(serve, 'dispatch', new_callable=AsyncMock) as dispatch:
            dispatch.side_effect = RuntimeError('renderer failed')
            response = await self.client.post('/render/broken_task', json={})

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()['detail'], 'RuntimeError: renderer failed')

    async def test_invalid_json_falls_back_to_empty_object(self):
        with patch.object(serve, 'dispatch', new_callable=AsyncMock) as dispatch:
            dispatch.return_value = ([b'image'], {})
            response = await self.client.post(
                '/render/song_info',
                content=b'{',
                headers={'Content-Type': 'application/json'},
            )

        self.assertEqual(response.status_code, 200)
        dispatch.assert_awaited_once_with('song_info', {})


if __name__ == '__main__':
    unittest.main()
