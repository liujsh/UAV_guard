"""Synchronous MessagePack-RPC client for a dedicated AirSim instance."""

import socket

import msgpack


class AirSimRPC:
    def __init__(self, host: str, port: int, timeout: float = 8.0) -> None:
        self._socket = socket.create_connection((host, port), timeout=timeout)
        self._unpacker = msgpack.Unpacker(raw=False, unicode_errors="surrogateescape",
                                           max_buffer_size=8 * 1024 * 1024)
        self._request_id = 0

    def __enter__(self) -> "AirSimRPC":
        return self

    def __exit__(self, *_exc_info) -> None:
        self._socket.close()

    def call(self, method: str, *args):
        self._request_id += 1
        self._socket.sendall(msgpack.packb([0, self._request_id, method, args], use_bin_type=True))
        while True:
            for response in self._unpacker:
                if not isinstance(response, list) or response[:2] != [1, self._request_id] or len(response) != 4:
                    raise RuntimeError(f"AirSim 返回无效响应：{method}")
                if response[2] is not None:
                    raise RuntimeError(f"AirSim {method}：{response[2]}")
                return response[3]
            data = self._socket.recv(65_536)
            if not data:
                raise ConnectionError(f"AirSim 在 {method} 调用中断开")
            self._unpacker.feed(data)
