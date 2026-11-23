"""和风天气：weather.now = GeoAPI 城市解析 + 实时天气 + 3 日预报。

未配置 api_host 或 key 时不注册任何能力（插件状态 needs_config）。
认证走 ``X-QW-Api-Key`` 请求头；key 永不出现在日志或错误里。
同一地点 10 分钟内复用缓存，节省免费额度。
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from whale.capabilities.spec import obj, s
from whale.kernel.errors import WhaleError
from whale.plugins.builtin._common import http_client, request_json

SERVICE = "和风天气"
CACHE_TTL_S = 600
_COORDS = re.compile(r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*[,，]\s*(-?\d{1,3}(?:\.\d+)?)\s*$")
# QWeather 业务码 → (错误码, 说明)
_API_CODES = {
    "204": ("not_found", "该地点暂无天气数据"),
    "400": ("invalid_params", "请求参数错误"),
    "401": ("unauthorized", "鉴权失败，请检查 API KEY"),
    "402": ("rate_limited", "额度已用尽"),
    "403": ("unauthorized", "无访问权限（检查 Host 与 KEY 是否匹配）"),
    "404": ("not_found", "查询的地点或数据不存在"),
    "429": ("rate_limited", "请求过于频繁"),
}


def normalize_host(host: str) -> str:
    h = (host or "").strip()
    for prefix in ("https://", "http://"):
        if h.startswith(prefix):
            h = h[len(prefix):]
    return h.strip().rstrip("/")


def parse_coords(text: str) -> str | None:
    """'lon,lat' 或 'lat,lon'（按中国范围判别）→ 'lon,lat'（两位小数）。"""
    m = _COORDS.match(text or "")
    if not m:
        return None
    a, b = float(m.group(1)), float(m.group(2))
    if 15 <= a <= 55 and 70 <= b <= 140:      # 先纬后经
        a, b = b, a
    if not (-180 <= a <= 180 and -90 <= b <= 90):
        return None
    return f"{a:.2f},{b:.2f}"


def check_code(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise WhaleError("unavailable", f"{SERVICE} 返回格式异常")
    code = str(data.get("code") or "")
    if code == "200":
        return data
    err, msg = _API_CODES.get(code, ("unavailable", f"服务返回错误码 {code or '缺失'}"))
    raise WhaleError(err, f"{SERVICE}：{msg}", retryable=err in ("rate_limited", "unavailable"),
                     details={"api_code": code})


def place_name(city: dict[str, Any]) -> str:
    name, adm2, adm1 = (str(city.get(k) or "") for k in ("name", "adm2", "adm1"))
    place = name
    if adm2 and adm2 not in place:
        place = adm2 + name
    if adm1 and adm1.rstrip("省市") not in place:
        place = f"{place}（{adm1}）"
    return place or "当前位置"


def parse_now(now: dict[str, Any]) -> dict[str, Any]:
    return {"text": now.get("text", ""), "temp": now.get("temp"), "feels_like": now.get("feelsLike"),
            "wind_dir": now.get("windDir", ""), "wind_scale": now.get("windScale", ""),
            "humidity": now.get("humidity"), "precip": now.get("precip"), "obs_time": now.get("obsTime", "")}


def parse_daily(days: list[Any]) -> list[dict[str, Any]]:
    return [{"date": d.get("fxDate", ""), "text_day": d.get("textDay", ""), "text_night": d.get("textNight", ""),
             "temp_min": d.get("tempMin"), "temp_max": d.get("tempMax"), "precip": d.get("precip"),
             "uv_index": d.get("uvIndex")} for d in days if isinstance(d, dict)][:3]


def brief(place: str, now: dict[str, Any], daily: list[dict[str, Any]]) -> str:
    bits = [str(now["text"])] if now.get("text") else []
    if now.get("temp") not in (None, ""):
        bits.append(f"{now['temp']}°C")
    if now.get("feels_like") not in (None, "", now.get("temp")):
        bits.append(f"体感{now['feels_like']}°C")
    if now.get("wind_dir") or now.get("wind_scale"):
        bits.append(f"{now.get('wind_dir', '')}{now.get('wind_scale', '')}级")
    if now.get("humidity") not in (None, ""):
        bits.append(f"湿度{now['humidity']}%")
    lines = [f"{place} 现在：" + ("，".join(bits) if bits else "暂无实况")]
    for label, day in zip(("今天", "明天", "后天"), daily):
        cond = day["text_day"]
        if day["text_night"] and day["text_night"] != cond:
            cond = f"{cond}转{day['text_night']}"
        line = f"{label}：{cond} {day['temp_min']}~{day['temp_max']}°C"
        if day.get("precip") not in (None, "", "0", "0.0"):
            line += f"，降水{day['precip']}mm"
        lines.append(line)
    return "\n".join(lines)


class QWeather:
    def __init__(self, app: Any, host: str, key: str) -> None:
        self.app = app
        self.base = f"https://{normalize_host(host)}"
        self.key = key
        self.cache: dict[str, tuple[float, dict[str, Any]]] = {}

    async def _get(self, client: Any, path: str, params: dict[str, str]) -> dict[str, Any]:
        return check_code(await request_json(client, "GET", self.base + path, SERVICE, params=params))

    async def lookup(self, client: Any, location: str) -> dict[str, Any]:
        coords = parse_coords(location)
        data = await self._get(client, "/geo/v2/city/lookup",
                               {"location": coords or location, "number": "1", "lang": "zh"})
        found = data.get("location")
        if not isinstance(found, list) or not found or not isinstance(found[0], dict) or not found[0].get("id"):
            raise WhaleError("not_found", f"{SERVICE}：找不到地点「{location}」")
        return found[0]

    async def query(self, location: str) -> dict[str, Any]:
        hit = self.cache.get(location)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        async with http_client(self.app, timeout=10, headers={"X-QW-Api-Key": self.key,
                                                              "Accept-Encoding": "gzip"}) as client:
            city = await self.lookup(client, location)
            lid = str(city["id"])
            now_raw, daily_raw = await asyncio.gather(
                self._get(client, "/v7/weather/now", {"location": lid, "lang": "zh"}),
                self._get(client, "/v7/weather/3d", {"location": lid, "lang": "zh"}))
        if not isinstance(now_raw.get("now"), dict):
            raise WhaleError("unavailable", f"{SERVICE}：实况数据缺失")
        now = parse_now(now_raw["now"])
        daily = parse_daily(daily_raw.get("daily") or [])
        place = place_name(city)
        result = {
            "location": {"id": lid, "name": city.get("name", ""), "adm1": city.get("adm1", ""),
                         "adm2": city.get("adm2", ""), "lat": city.get("lat"), "lon": city.get("lon")},
            "now": now, "forecast": daily, "updated": now_raw.get("updateTime", ""),
            "source": "和风天气", "summary": brief(place, now, daily)}
        self.cache[location] = (time.monotonic() + CACHE_TTL_S, result)
        return result


def setup(p: Any) -> None:
    host, key = normalize_host(str(p.config.get("api_host") or "")), p.secret("key")
    if not host or not key:
        p.log.info("qweather not configured (api_host / key missing)")
        return
    default_city = str(p.config.get("default_city") or "").strip()
    client = QWeather(p.app, host, key)

    @p.capability("weather.now",
                  "查询某地的实时天气与未来 3 天预报（和风天气）。location 可以是城市/区县名，或 '经度,纬度'；"
                  "不填则查询默认城市。",
                  params=obj(location=s("城市名（如 北京、杭州西湖区）或 '经度,纬度'", default="")),
                  title="查询天气", timeout_s=25)
    async def weather_now(args: dict[str, Any], ctx: Any) -> Any:
        location = str(args.get("location") or "").strip() or default_city
        if not location:
            raise WhaleError("invalid_params", "请提供要查询的城市（插件未设置默认城市）")
        return await client.query(location[:80])
