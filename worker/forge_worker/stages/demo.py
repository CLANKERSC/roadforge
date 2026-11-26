"""M0 demo: a fake 3-stage job that exercises progress, logs, cancel, failure and resume."""

from __future__ import annotations

from forge_worker.pipeline import Pipeline, Stage, StageContext, register

DEFAULT_STEPS = 10
DEFAULT_STEP_SECONDS = 0.4


def _fake_stage(name: str) -> Stage:
    def run(ctx: StageContext) -> None:
        steps = int(ctx.params.get("steps", DEFAULT_STEPS))
        step_seconds = float(ctx.params.get("step_seconds", DEFAULT_STEP_SECONDS))
        fail_at = ctx.params.get("fail_at_stage")
        ctx.log(f"{name}: starting ({steps} steps)")
        for i in range(steps):
            ctx.check_cancelled()
            if fail_at == name and i == steps // 2:
                raise RuntimeError(f"Simulated failure in stage '{name}'")
            ctx.sleep(step_seconds)
            ctx.report((i + 1) / steps, f"{name}: step {i + 1} of {steps}")
        ctx.log(f"{name}: finished")

    return Stage(id=name, label=name.capitalize(), run=run)


DEMO = register(
    Pipeline(
        kind="demo",
        label="Demo job (fake 3 stages)",
        stages=(_fake_stage("fetch"), _fake_stage("process"), _fake_stage("package")),
    )
)
