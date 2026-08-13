from pathlib import Path


RUNTIME_PATHS = tuple(
    Path(value)
    for value in (
        "AGENTS.md",
        ".agents/skills/a-share",
        "模板",
        "scripts/init_workspace.py",
        "scripts/migrate_workspace.py",
        "scripts/security_scan.py",
        "scripts/shadow_replay_workspace.py",
        "scripts/validate_deployment.py",
        "scripts/validate_release.py",
        "docs/architecture.md",
        "docs/shadow-replay-acceptance.md",
        *(f"docs/adr/{number:04d}-{name}.md" for number, name in (
            (12, "文档事实源与可重建检索投影分离"),
            (13, "复合投研采用阶段上下文隔离"),
            (14, "检索以原子研究单元为权威粒度"),
            (15, "检索按字段权威映射去重"),
            (16, "来源载荷外置并按需核验"),
            (17, "上下文预算不作为硬阻断条件"),
            (18, "权威研究写入与叙事呈现分阶段"),
            (19, "以深模块装配投研工作集"),
            (20, "Augment仅作为可替换语义adapter"),
            (21, "本地结构化投影采用SQLite与FTS5"),
            (22, "陈旧检索投影禁用并回退事实源"),
            (23, "历史产物彻底结构迁移并移除兼容"),
            (24, "影子回放验收后无兼容切换"),
            (25, "任务证据清单由版本化任务契约定义"),
            (26, "持久任务保存可视化就绪的工作集清单"),
            (27, "工作集装配作为skill套件内部Python-module"),
            (28, "先完成实现与影子迁移再申请正式切换"),
        )),
    )
)
