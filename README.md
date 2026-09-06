# 📬 jlc自动签到脚本

使用此 Python 脚本可以自动为多个jlc账号完成每日签到任务，并通过 [Server 酱](https://sct.ftqq.com/)推送签到结果至微信。

---

## ✨ 项目功能

* ✅ 支持 **多个账号并发签到**
* 🎁 自动判断是否为**第七天签到**并领取8个金豆
* 💰 获取并显示当前账号金豆数量
* 📊 将账号按通知 `SendKey` 分组，分组推送签到结果
* 📲 使用 **Server 酱** 实时推送签到通知到微信

---

## 🔧 使用前准备

### 1️⃣ 获取 AccessToken

1. 打开 [jlc官网](https://m.jlc.com)
2. 登录账号，按 `F12` 打开浏览器开发者工具
3. 在「Network」中找到任意请求
4. 查看请求头 Header 中的 `X-JLC-AccessToken`，复制其值
5. 多个账号用英文逗号 `,` 分隔
6. 建议使用手机抓包，请勿手动退出账号，否则会导致token失效

脚本会按当前 Web 端协议自动获取动态 `secretkey`，无需把 `secretkey` 保存到 GitHub Secrets。

### 2️⃣ 获取 SendKey（Server 酱）

1. 打开 [Server 酱官网](https://sct.ftqq.com/)
2. 注册并登录后，进入「发送通道」页面
3. 创建通知通道并获取 `SendKey`
4. 多个账号用英文逗号 `,` 分隔，**数量需与 TOKEN\_LIST 一一对应**

---

## ⚙️ 配置说明

### GitHub Actions 配置

1. Fork 本仓库
2. 进入仓库 Settings → Secrets → Actions
3. 添加以下 Secrets：

| 变量名             | 示例值           | 说明               |
| --------------- | ------------- | ---------------- |
| TOKEN\_LIST     | token1,token2 | 多个 token 用英文逗号分隔 |
| SEND\_KEY\_LIST | key1,key2     | 与 token 一一对应     |
### token 用英文逗号分隔！不需要加单引号双引号。多个账号之间用逗号隔开就好了。有几个账号，就贴几个通知KEY（可以相同，也可以不同。不同的没测试过不一定能用）。不然跑不起来
### 本地运行配置

```ini
TOKEN_LIST这样填：your_token1,your_token2
SEND_KEY_LIST这样填：your_key1,your_key2
```
---

## 📋 执行流程

1. 获取动态 `secretkey` 并验证 AccessToken
2. 查询签到状态，未签到时按 Web 端参数执行签到
3. 查询签到前后金豆余额并推送实际变化
4. 任一账号失败时发送异常通知，并让 GitHub Actions 返回失败状态

### 常见问题

如果日志提示 `HTTP 401` 或“用户未登录或会话失效”，请重新登录嘉立创，重新抓取
`X-JLC-AccessToken`，再更新仓库的 `TOKEN_LIST` Secret。不要把 AccessToken 写进代码、
Issue 或公开日志。

---

## ⏱️ GitHub Actions 定时运行

### 创建 `.github/workflows/jlc-signin.yml`

```yaml
name: JLC Auto Sign

on:
  schedule:
    - cron: '0 16,22,4,10 * * *'  # 每 6 小时执行一次，UTC + 8 北京时间

  workflow_dispatch:

jobs:
  sign:
    runs-on: ubuntu-latest
    steps:
    - name: Checkout
      uses: actions/checkout@v3

    - name: Run Sign Script
      env:
        TOKEN_LIST: ${{ secrets.TOKEN_LIST }}
        SEND_KEY_LIST: ${{ secrets.SEND_KEY_LIST }}
      run: |
        python main.py
```

---

## 📄 License

本项目遵循 MIT License，欢迎自由使用和修改。
