# 内容工作室 · Content Studio

![Content Studio](assets/logo.png)

一个面向中文创作者的 Agent Skills 插件，版本 **0.1.0**。

输入主题、读者和账号风格，得到选题、图文草稿或短视频脚本；可用随附离线工具检查固定标签、禁用短语、正文长度、文本重复和视觉简报的一致性。

## 组成

- `plugin.json`：Agent Plugins 标准清单。
- `skills/content-studio/SKILL.md`：创作、审稿与修订方法。
- `skills/content-studio/scripts/audit_content.py`：Python 标准库离线检查器。
- `skills/content-studio/examples/`：正常示例与故意包含问题的示例，均为合成数据。
- `tests/`：真实启动 CLI 的黑盒测试。

本插件没有托管服务器、MCP 服务、API Key、账号连接器和自动发布功能。文字创作依赖宿主模型及用户自己的使用额度；离线检查器不调用模型、不联网。

## 使用

在支持 Agent Plugins 的宿主中加载此插件后，调用 `content-studio` 技能。例如：

> 用 content-studio 为写作新手策划 3 个“下班后记录生活”的选题，展开 1 篇图文。语气自然，固定标签 #创作练习。先给草稿和待核实事项，不发布。

> 用 content-studio 检查我提供的 3 条草稿。保留固定标签和角色身份，比较背景、构图、服装是否重复。保留原稿，另存修订版和检查报告。

### Grok Bot 试用

本次验证使用完整技能文件所在的 Bot 工作区，以及一个手动调用的私有适配入口。适配入口先读取原 `SKILL.md` 和参考文件、核验检查器哈希，再运行脚本。保存入口后，已通过明确名称调用生成新草稿并实际检查。

试用时，需要将 `skills/content-studio/` 中的完整资源保存到宿主允许的工作目录，再让私有适配入口引用这些文件。入口应只在用户主动调用时执行；文件缺失或哈希不符时明确报告不可用，并保留原稿、另存报告。只保存一份技能说明文字不等于已安装离线检查器。

本次 Grok Bot 环境无法读取上传的 ZIP 附件，因此改用逐文件核验哈希的纯文本源码包交接；原生私有技能保存功能没有同时携带脚本和参考资源。私有库由各 Bot 共享，当前桌面的 `/` 菜单也没有展示测试入口，所以实际验收采用明确名称调用，未修改其他 Bot 的设置。

这些结果验证了 Bot 工作区中的文件运行及私有入口调用，**不代表从官方市场安装通过**。宿主界面、文件保留和技能入口能力可能随版本变化；正式分发还需目标客户端的安装验证。

## 离线检查（无需宿主模型）

要求 Python 3.10+，没有需要安装的第三方依赖。在本目录执行：

```sh
python skills/content-studio/scripts/audit_content.py --input skills/content-studio/examples/sample-good.json
python skills/content-studio/scripts/audit_content.py --input skills/content-studio/examples/sample-problems.json
python -m unittest discover -s tests -v
```

Linux/macOS 可能需要将 `python` 换为 `python3`。Windows 如果 `python` 是无效的商店别名，请使用已安装 Python 的完整路径，无需改变系统默认环境。

传入 `--output 新报告.json` 可保存 JSON 报告。父目录须已存在；输出路径必须不存在，已有文件、目录或符号链接一律拒绝，原稿也不能指定为输出。写入异常时可能留下不完整的新报告，工具将明确报错。

退出码：`0` 已执行规则无 error（仍可有 warning）；`1` 检出内容错误；`2` 输入或文件操作失败。运行故意有问题的示例预期返回 `1`，这表示检查器正确检出了问题。

输入格式见 [input-format.md](skills/content-studio/references/input-format.md)。最大输入 5 MiB，正文每条最多 100000 字符，`posts` 与 `history` 合计最多 1000 条；日常建议每批 3–20 条。

## 已知限制

- 文本重复使用字符统计启发式，可能漏掉语义相同但改写较多的内容，需人工阅读。
- 视觉检查只比较文字简报，不识别图片，不保证生成角色一致。
- 不自动核实事实、引用、版权、最新平台规则或创作质量，也不承诺流量和收益。
- 只有用户给出的历史记录参与检查，未提供历史时不声称检查了账号全部作品。
- 不自动存储账号档案，不读取其他文件，不采集使用数据。

## 已验证的测试

- Windows / Python 3.12.14：42 项 CLI 测试全部通过。
- Grok Bot 工作区 / Python 3.13.5：同一版本 42 项测试全部通过。
- 正常示例返回退出码 `0`，无错误或警告；故意包含问题的示例返回 `1`，检出预期的 6 个错误。
- 已存在的报告路径被拒绝写入，返回 `2`；原稿和已有报告保持不变。
- Grok Bot 已完成三条合成图文的实际检查，以及保存私有适配入口后的新主题调用。短视频口播、字幕字段的长度和禁词边界也已验证。

上述结果不包含官方市场安装、图像或视频渲染、自动发布或收益验证。测试命令和合成示例都随仓库提供。

## 分发状态

公开源码仓库：[nkopgzm/content-studio](https://github.com/nkopgzm/content-studio)。
源码公开不代表已获官方市场审核；是否已上架请以官方插件目录的实际展示为准。

参考：
- [Agent Plugins 标准](https://agent-plugins.org/plugin-authors/build-an-agent-plugin)
- [Grok Bot 技能说明](https://docs.x.ai/grok-bot/skills-routines-and-automations)
- [官方插件提交表](https://x.ai/bot/marketplace/submit-a-plugin)

## 许可与隐私

源码与本项目原创示例使用 MIT 许可，见 `LICENSE`。用户提供的原稿和参考素材仍属于其各自权利人。
数据处理说明见 `PRIVACY.md`。本插件不附带对宿主服务的许可或免费额度承诺。
