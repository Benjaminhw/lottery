# 喜礼现场

面向婚礼现场的扫码签到与喜礼抽奖应用。宾客扫码后使用姓名或微信昵称签到，管理端实时看到来宾名单并按轮次开奖；结果由服务端安全随机产生、持久化且跨轮不重复。

## 现场能力

- 多场婚礼、签到开关、喜礼轮次、奖品和人数配置
- 自动生成签到二维码，宾客名单实时推送到管理台与喜礼大屏
- 姓名签到，以及微信公众号 `snsapi_userinfo` 昵称/头像授权
- 服务端原子抽奖、顺序开奖、跨轮不重复、重复请求不重抽
- 候选头像滚动、减速揭晓、多中奖者展示、全屏与结果回看
- SQLite WAL 持久化、管理员会话、生产安全校验、Docker 或 systemd 部署
- 同场婚礼共享一条数据库轮询，多个 SSE 客户端复用快照

## 婚礼流程

1. 管理员创建婚礼，默认带“欢喜伴手礼、甜蜜幸运奖、幸福锦鲤”三轮，可按实际奖品修改。
2. 展示或下载签到二维码，宾客扫码填写姓名或使用微信资料签到。
3. 人数确认后关闭签到，打开喜礼大屏并切换全屏。
4. 按顺序开奖；第一次开奖也会自动关闭签到入口。
5. 在管理台查看结果。彩排后可重置全部结果，签到名单会保留。

## 本地运行

要求 Python 3.11 或更高版本。

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env
uvicorn app.main:app --reload
```

访问 `http://127.0.0.1:8000/admin`。开发模板密码 `change-me` 只能用于本地。

## 当前服务器部署

当前服务器使用用户级 systemd 运行应用，并通过已有 Nginx 的 `/wedding/` 子路径提供 HTTPS：

```bash
mkdir -p ~/.config/systemd/user
ln -sf ~/Learning/lottery/deploy/lottery.service ~/.config/systemd/user/lottery.service
systemctl --user daemon-reload
systemctl --user enable --now lottery.service
```

安装独立的 Nginx 站点文件。它只接管 `8.133.234.5`，将 `/wedding/` 转给喜礼现场，其余路径继续转给现有知卡服务：

```bash
sudo install -m 644 deploy/nginx-wedding-site.conf /etc/nginx/conf.d/wedding.conf
sudo nginx -t
sudo systemctl reload nginx
```

生产 `.env` 关键项：

```dotenv
APP_ENV=production
PUBLIC_BASE_URL=https://8.133.234.5/wedding
BASE_PATH=/wedding
DATABASE_PATH=./data/lottery.db
COOKIE_SECURE=true
```

`ADMIN_PASSWORD` 与 `SECRET_KEY` 必须使用随机强值。生产模式发现默认值时会拒绝启动。

## Docker 部署

```bash
cp .env.example .env
# 修改密码、密钥和外部 HTTPS 地址
docker compose up -d --build
```

Compose 只绑定 `127.0.0.1:8000`，公网流量应始终经过 HTTPS 反向代理。根路径部署时将 `BASE_PATH` 留空。

## 微信签到

自动读取昵称和头像需要具备网页授权能力的微信公众号：在公众平台配置网页授权域名，填写 `WECHAT_APP_ID`、`WECHAT_APP_SECRET`，并把 `PUBLIC_BASE_URL` 设为同一 HTTPS 域名。子路径部署的回调地址为 `https://你的域名/wedding/auth/wechat/callback`。

未配置微信参数时，微信签到按钮自动隐藏，姓名签到仍可使用。普通个人微信或未开通网页授权的订阅号不能读取昵称与头像。

## 数据与测试

- 数据库位于 `DATABASE_PATH`；正式婚礼前后应备份数据库文件及 `-wal`、`-shm` 文件，或停服务后复制整个 `data/` 目录。
- 同一婚礼内姓名唯一；同名宾客可在姓名后附加关系或编号。
- 微信 OpenID 只用于防重复，不会由公开 API 返回。

```bash
.venv313/bin/python -m pytest
```

100/200 人并发实测数据与配置建议见 `deploy/CAPACITY.md`。