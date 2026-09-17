"""根目录入口 shim。

真正的应用定义在 `app/main.py`，这里只做重新导出，方便：
- `uvicorn main:app`（部分托管平台只识别根目录 main.py）
- `python main.py` 本地直接起服务

不要在本文件里写业务逻辑。
"""

from app.main import app  # noqa: F401

if __name__ == "__main__":
    import os

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=bool(os.getenv("RELOAD")),
    )
