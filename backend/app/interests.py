"""新用户兴趣选择：兴趣跟随会话JWT（新增interests claim），不改数据库结构。

设计说明：accounts表结构与课程数据表均为验收基线（只读），因此注册时选择的
兴趣类型存放在会话令牌中，由 /me/interests 端点重新签发会话并刷新CSRF。
退出登录（auth_version自增）后兴趣随会话失效，可重新设置。
"""
from fastapi import APIRouter, Request, Response
from pydantic import StrictStr, field_validator
from pydantic import Field

from .errors import APIError
from .schemas import Input
from .security import (SESSION_COOKIE, COOKIE_PATH, ActorDependency, decode, encode,
    issue_csrf)

router = APIRouter(tags=["兴趣画像"])

# 与训练目录的对齐类型集合一致（MovieLens 19类，排除 '(no genres listed)'）
CANONICAL_GENRES = ("Action", "Adventure", "Animation", "Children", "Comedy", "Crime",
    "Documentary", "Drama", "Fantasy", "Film-Noir", "Horror", "IMAX", "Musical",
    "Mystery", "Romance", "Sci-Fi", "Thriller", "War", "Western")


class InterestInput(Input):
    genres: list[StrictStr] = Field(min_length=1, max_length=8)

    @field_validator("genres")
    @classmethod
    def known_genres(cls, value: list[str]) -> list[str]:
        if any(g not in CANONICAL_GENRES for g in value):
            raise ValueError("包含未知的电影类型")
        return list(dict.fromkeys(value))


def _session_claims(request: Request) -> dict:
    try:
        return decode(request, request.cookies.get(SESSION_COOKIE, ""), "course-api", "session")
    except Exception:  # noqa: BLE001 - 无会话或过期一律视为无兴趣
        return {}


def current_interests(request: Request) -> list[str]:
    """从会话令牌读取兴趣；仅供推荐路由注入provider context。"""
    values = _session_claims(request).get("interests")
    if not isinstance(values, list):
        return []
    return [g for g in values if isinstance(g, str) and g in CANONICAL_GENRES]


@router.get("/me/interests")
def get_interests(request: Request, actor: ActorDependency):
    return {"genres": current_interests(request), "selectable": list(CANONICAL_GENRES)}


@router.post("/me/interests")
def set_interests(body: InterestInput, request: Request, response: Response, actor: ActorDependency):
    claims = _session_claims(request)
    if claims.get("kind") != "session" or not isinstance(claims.get("sub"), str) or not isinstance(claims.get("ver"), int) or not isinstance(claims.get("jti"), str):
        raise APIError(401, "AUTHENTICATION_REQUIRED", "请先登录或重新登录")
    token = encode(request, {"sub": claims["sub"], "ver": claims["ver"], "jti": claims["jti"],
        "interests": body.genres}, "course-api", "session")
    cfg = request.app.state.settings
    response.set_cookie(SESSION_COOKIE, token, max_age=cfg.jwt_minutes * 60, httponly=True,
        secure=cfg.cookie_secure, samesite="strict", path=COOKIE_PATH)
    return {"genres": body.genres, "csrf_token": issue_csrf(request, response, token)}
