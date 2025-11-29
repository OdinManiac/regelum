from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import pygame

from rg_compiler.core.contracts import contract
from rg_compiler.core.core_node import Input
from rg_compiler.core.ext_node import ExtNode
from rg_compiler.core.node import Context


@dataclass(frozen=True)
class DashboardSignal:
    name: str
    label: str
    color: Tuple[int, int, int]
    time_window: Optional[float] = None


class DashboardPlotter(ExtNode):
    dt = Input[float](default=0.01)

    def __init__(
        self,
        node_id: str,
        signals: Sequence[DashboardSignal],
        *,
        width: int = 1100,
        height: int = 720,
        max_points: int = 200000,
        time_window: Optional[float] = 12.0,
    ):
        super().__init__(node_id)
        self._signals = list(signals)
        self._width = width
        self._height = height
        self._max_points = max_points
        self._time_window = time_window
        self._time = 0.0
        self.paused = False
        self._initialized = False
        self._screen: pygame.Surface | None = None
        self._font_small: pygame.font.Font | None = None
        self._font_large: pygame.font.Font | None = None
        self._histories: Dict[str, List[Tuple[float, float]]] = {sig.name: [] for sig in self._signals}
        self._bg_color = (16, 16, 18)
        self._grid_color = (60, 60, 70)
        self._axis_color = (200, 200, 210)
        self._button_rect: pygame.Rect | None = None
        for sig in self._signals:
            self.add_input(sig.name, default=0.0)

    def _init_display(self) -> None:
        pygame.init()
        self._screen = pygame.display.set_mode((self._width, self._height))
        pygame.display.set_caption("Dashboard")
        self._font_small = pygame.font.Font(None, 18)
        self._font_large = pygame.font.Font(None, 22)
        self._initialized = True
        self._button_rect = pygame.Rect(self._width - 120, 12, 100, 32)

    def _append_sample(self, name: str, sample: Tuple[float, float]) -> None:
        history = self._histories[name]
        history.append(sample)
        if len(history) > self._max_points:
            history.pop(0)

    def _visible(self, name: str, series: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
        sig_window: Optional[float] = None
        for sig in self._signals:
            if sig.name == name:
                sig_window = sig.time_window
                break
        window = sig_window if sig_window is not None else self._time_window
        if window is None:
            return series
        start = self._time - window
        return [p for p in series if p[0] >= start]

    def _panel_rects(self) -> List[pygame.Rect]:
        count = len(self._signals)
        margin = 16
        if count == 0:
            return []
        panel_height = (self._height - margin * (count + 1)) // count
        rects: List[pygame.Rect] = []
        top = margin
        for _ in range(count):
            rects.append(pygame.Rect(margin, top, self._width - margin * 2, panel_height))
            top += panel_height + margin
        return rects

    def _draw_axes(self, rect: pygame.Rect, x_ticks: List[float], y_ticks: List[float], x0: float, x1: float) -> None:
        if self._screen is None:
            return
        pygame.draw.rect(self._screen, self._bg_color, rect)
        pygame.draw.rect(self._screen, self._grid_color, rect, width=1)
        for xt in x_ticks:
            x = rect.left + int((xt - x0) / (x1 - x0) * rect.width)
            pygame.draw.line(self._screen, self._grid_color, (x, rect.top), (x, rect.bottom))
        y_span = y_ticks[-1] - y_ticks[0] if len(y_ticks) > 1 else 1.0
        for yt in y_ticks:
            y = rect.bottom - int((yt - y_ticks[0]) / (y_span + 1e-9) * rect.height)
            pygame.draw.line(self._screen, self._grid_color, (rect.left, y), (rect.right, y))

    def _plot_series(
        self,
        rect: pygame.Rect,
        name: str,
        series: List[Tuple[float, float]],
        label: str,
        color: Tuple[int, int, int],
    ) -> None:
        if self._screen is None or self._font_small is None or not series:
            return

        visible = self._visible(name, series)
        if not visible:
            return

        xs = [p[0] for p in visible]
        ys = [p[1] for p in visible]
        x0 = xs[0]
        x1 = xs[-1]
        if x1 - x0 < 1e-6:
            x1 = x0 + 1.0

        y_abs_max = max(abs(v) for v in ys)
        y_range = y_abs_max if y_abs_max > 1e-6 else 1.0
        y_min = -y_range
        y_max = y_range

        x_ticks = [round(x0 + i * (x1 - x0) / 4, 2) for i in range(5)]
        y_ticks = [round(y_min + i * (y_max - y_min) / 4, 2) for i in range(5)]
        self._draw_axes(rect, x_ticks, y_ticks, x0, x1)

        pts: List[Tuple[int, int]] = []
        for t, v in visible:
            px = rect.left + int((t - x0) / (x1 - x0) * rect.width)
            py = rect.bottom - int((v - y_min) / (y_max - y_min) * rect.height)
            pts.append((px, py))

        if len(pts) > 1:
            pygame.draw.lines(self._screen, color, False, pts, 2)

        title = f"{label}: {ys[-1]:.3f}"
        if self._font_large:
            text = self._font_large.render(title, True, color)
            self._screen.blit(text, (rect.left + 8, rect.top + 6))
        x_label = self._font_small.render(f"time [{x0:.1f}..{x1:.1f}] s", True, self._axis_color)
        y_label = self._font_small.render(f"range [{y_min:.2f}; {y_max:.2f}]", True, self._axis_color)
        self._screen.blit(x_label, (rect.right - x_label.get_width() - 8, rect.bottom - x_label.get_height() - 6))
        if self._font_large:
            font_height = self._font_large.get_height()
        else:
            font_height = 0
        self._screen.blit(y_label, (rect.right - y_label.get_width() - 8, rect.top + font_height + 4))

    def _draw_button(self) -> None:
        if self._screen is None or self._font_large is None or self._button_rect is None:
            return
        color = (60, 140, 60) if self.paused else (180, 70, 70)
        pygame.draw.rect(self._screen, color, self._button_rect, border_radius=6)
        label = "Play" if self.paused else "Pause"
        text = self._font_large.render(label, True, (240, 240, 240))
        text_pos = text.get_rect(center=self._button_rect.center)
        self._screen.blit(text, text_pos)

    def _handle_events(self) -> None:
        if self._button_rect is None:
            return
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                raise SystemExit
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if self._button_rect.collidepoint(event.pos):
                    self.paused = not self.paused

    @contract(no_instant_loop=False, deterministic=True, no_side_effects=False)
    def step(self, ctx: Context):
        if not self._initialized:
            self._init_display()

        dt_val = ctx.read(self.inputs["dt"])
        self._handle_events()
        if self._screen is None:
            return

        self._time += dt_val
        for sig in self._signals:
            val = ctx.read(self.inputs[sig.name])
            self._append_sample(sig.name, (self._time, val))

        self._screen.fill((10, 10, 12))
        panels = self._panel_rects()
        for rect, sig in zip(panels, self._signals):
            history = self._histories[sig.name]
            self._plot_series(rect, sig.name, history, sig.label, sig.color)
        self._draw_button()
        pygame.display.flip()

    def render_static(self) -> None:
        if not self._initialized:
            self._init_display()
        self._handle_events()
        if self._screen is None:
            return
        self._screen.fill((10, 10, 12))
        panels = self._panel_rects()
        for rect, sig in zip(panels, self._signals):
            history = self._histories[sig.name]
            self._plot_series(rect, sig.name, history, sig.label, sig.color)
        self._draw_button()
        pygame.display.flip()


def build_dashboard(node_id: str, signals: Sequence[DashboardSignal], **kwargs) -> DashboardPlotter:
    return DashboardPlotter(node_id, signals, **kwargs)
