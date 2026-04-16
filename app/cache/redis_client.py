from typing import Optional

from redis import Redis

from app.core.config import Settings


def build_redis_client(settings: Settings) -> Redis:
    password: Optional[str] = settings.redis_password or None
    return Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        password=password,
        decode_responses=False,
        socket_timeout=2.0,
        socket_connect_timeout=2.0,
    )
