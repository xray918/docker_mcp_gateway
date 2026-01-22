"""反向代理模块

支持 HTTP/SSE/WebSocket 请求透传到 Docker 容器。
"""

import asyncio
import logging
from typing import Any, AsyncGenerator

import httpx
from fastapi import Request, Response, WebSocket, WebSocketDisconnect
from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)

# HTTP 客户端配置
DEFAULT_TIMEOUT = 300.0  # 5分钟，MCP 调用可能比较慢
DEFAULT_CONNECT_TIMEOUT = 10.0


class ProxyClient:
    """代理客户端"""
    
    def __init__(self):
        self._client: httpx.AsyncClient | None = None
    
    @property
    def client(self) -> httpx.AsyncClient:
        """获取 HTTP 客户端（延迟初始化）"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=DEFAULT_CONNECT_TIMEOUT,
                    read=DEFAULT_TIMEOUT,
                    write=DEFAULT_TIMEOUT,
                    pool=DEFAULT_TIMEOUT,
                ),
                follow_redirects=True,
            )
        return self._client
    
    async def close(self) -> None:
        """关闭客户端"""
        if self._client:
            await self._client.aclose()
            self._client = None
    
    async def proxy_request(
        self,
        request: Request,
        target_url: str,
    ) -> Response:
        """代理 HTTP 请求
        
        Args:
            request: FastAPI 请求对象
            target_url: 目标 URL
            
        Returns:
            Response: 响应对象
        """
        # 构建请求 URL
        # target_url 是基础 URL，需要加上请求路径的剩余部分
        full_url = target_url
        
        # 获取查询参数
        if request.query_params:
            full_url += f"?{request.query_params}"
        
        # 获取请求头（过滤掉 hop-by-hop 头）
        headers = dict(request.headers)
        hop_by_hop = {
            'connection', 'keep-alive', 'proxy-authenticate',
            'proxy-authorization', 'te', 'trailers', 'transfer-encoding',
            'upgrade', 'host'
        }
        headers = {
            k: v for k, v in headers.items()
            if k.lower() not in hop_by_hop
        }
        
        # 获取请求体
        body = await request.body()
        
        logger.debug(
            "代理请求: %s %s -> %s",
            request.method,
            request.url.path,
            full_url
        )
        
        try:
            # 发送请求
            response = await self.client.request(
                method=request.method,
                url=full_url,
                headers=headers,
                content=body,
            )
            
            # 检查是否是 SSE 响应
            content_type = response.headers.get('content-type', '')
            if 'text/event-stream' in content_type:
                return await self._handle_sse_response(response)
            
            # 普通响应
            response_headers = dict(response.headers)
            # 移除 hop-by-hop 头
            response_headers = {
                k: v for k, v in response_headers.items()
                if k.lower() not in hop_by_hop and k.lower() != 'content-encoding'
            }
            
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=response_headers,
            )
            
        except httpx.ConnectError as e:
            logger.error("连接失败: %s -> %s", full_url, e)
            return Response(
                content=f'{{"error": "容器连接失败: {e}"}}',
                status_code=502,
                media_type="application/json",
            )
        except httpx.TimeoutException as e:
            logger.error("请求超时: %s", full_url)
            return Response(
                content=f'{{"error": "请求超时"}}',
                status_code=504,
                media_type="application/json",
            )
        except Exception as e:
            logger.exception("代理请求失败: %s", full_url)
            return Response(
                content=f'{{"error": "代理错误: {e}"}}',
                status_code=500,
                media_type="application/json",
            )
    
    async def _handle_sse_response(
        self,
        response: httpx.Response,
    ) -> StreamingResponse:
        """处理 SSE 响应"""
        async def generate() -> AsyncGenerator[bytes, None]:
            async for chunk in response.aiter_bytes():
                yield chunk
        
        headers = dict(response.headers)
        headers = {
            k: v for k, v in headers.items()
            if k.lower() not in {'transfer-encoding', 'content-encoding'}
        }
        
        return StreamingResponse(
            generate(),
            status_code=response.status_code,
            headers=headers,
            media_type="text/event-stream",
        )
    
    async def proxy_streaming_request(
        self,
        request: Request,
        target_url: str,
    ) -> StreamingResponse:
        """代理流式请求（用于 MCP Streamable HTTP）
        
        Args:
            request: FastAPI 请求对象
            target_url: 目标 URL
            
        Returns:
            StreamingResponse: 流式响应
        """
        full_url = target_url
        if request.query_params:
            full_url += f"?{request.query_params}"
        
        headers = dict(request.headers)
        hop_by_hop = {
            'connection', 'keep-alive', 'proxy-authenticate',
            'proxy-authorization', 'te', 'trailers', 'transfer-encoding',
            'upgrade', 'host'
        }
        headers = {
            k: v for k, v in headers.items()
            if k.lower() not in hop_by_hop
        }
        
        body = await request.body()
        
        logger.debug("代理流式请求: %s -> %s", request.method, full_url)
        
        async def stream_response() -> AsyncGenerator[bytes, None]:
            try:
                async with self.client.stream(
                    method=request.method,
                    url=full_url,
                    headers=headers,
                    content=body,
                ) as response:
                    async for chunk in response.aiter_bytes():
                        yield chunk
            except Exception as e:
                logger.error("流式请求失败: %s", e)
                yield f'{{"error": "{e}"}}'.encode()
        
        return StreamingResponse(
            stream_response(),
            media_type="application/json",
        )


class WebSocketProxy:
    """WebSocket 代理"""
    
    async def proxy_websocket(
        self,
        websocket: WebSocket,
        target_url: str,
    ) -> None:
        """代理 WebSocket 连接
        
        Args:
            websocket: FastAPI WebSocket 对象
            target_url: 目标 WebSocket URL (ws://...)
        """
        await websocket.accept()
        
        logger.debug("WebSocket 代理: -> %s", target_url)
        
        try:
            import websockets
            
            async with websockets.connect(target_url) as target_ws:
                # 双向转发
                async def forward_to_target():
                    try:
                        while True:
                            data = await websocket.receive_text()
                            await target_ws.send(data)
                    except WebSocketDisconnect:
                        pass
                    except Exception as e:
                        logger.debug("Forward to target error: %s", e)
                
                async def forward_to_client():
                    try:
                        async for message in target_ws:
                            if isinstance(message, str):
                                await websocket.send_text(message)
                            else:
                                await websocket.send_bytes(message)
                    except Exception as e:
                        logger.debug("Forward to client error: %s", e)
                
                # 同时运行两个方向的转发
                await asyncio.gather(
                    forward_to_target(),
                    forward_to_client(),
                    return_exceptions=True,
                )
                
        except WebSocketDisconnect:
            logger.debug("WebSocket 客户端断开")
        except Exception as e:
            logger.error("WebSocket 代理错误: %s", e)
            try:
                await websocket.close(code=1011, reason=str(e))
            except Exception:
                pass


# 全局代理客户端
_proxy_client: ProxyClient | None = None
_websocket_proxy: WebSocketProxy | None = None


def get_proxy_client() -> ProxyClient:
    """获取代理客户端单例"""
    global _proxy_client
    if _proxy_client is None:
        _proxy_client = ProxyClient()
    return _proxy_client


def get_websocket_proxy() -> WebSocketProxy:
    """获取 WebSocket 代理单例"""
    global _websocket_proxy
    if _websocket_proxy is None:
        _websocket_proxy = WebSocketProxy()
    return _websocket_proxy


async def cleanup_proxy() -> None:
    """清理代理资源"""
    global _proxy_client
    if _proxy_client:
        await _proxy_client.close()
        _proxy_client = None
