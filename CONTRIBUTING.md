# Contributing

感谢改进这套 A 股投研工作流。

## 不得提交

- 个人持仓、成本、身份信息、API key 或未脱敏的运行记录。
- 受限行情、付费研报、未获授权数据或大段受版权保护内容。
- 将传闻、主力意图或单一价格变动写成已证实事实的规则。
- 绕过弃权、价格纪律、双轴复盘或 L3 护栏的修改。

## 提交前检查

```bash
python3 scripts/init_workspace.py --root .
python3 .agents/skills/a-share/shared/scripts/validate_workspace.py --root .
python3 scripts/validate_release.py
python3 -m py_compile scripts/init_workspace.py \
  scripts/migrate_workspace.py \
  scripts/validate_release.py \
  .agents/skills/a-share/shared/context/*.py \
  .agents/skills/a-share/shared/scripts/context_workspace.py \
  .agents/skills/a-share/shared/scripts/next_id.py \
  .agents/skills/a-share/shared/scripts/validate_workspace.py
python3 -m unittest discover -s tests -p 'test_*.py'
```

工作集测试只通过 `assemble` / `hydrate` 和 `context_workspace.py` JSON CLI；迁移测试必须使用隔离临时目录，不得读取或写入真实个人研究工作区。

新策略必须从 `trial` 或影子版本开始；新市场教训必须附证据簇、反例和适用边界。
