import asyncio
import random

from playwright.async_api import ElementHandle, Page

from ..session.state import SessionState


class Humanizer:
    def __init__(self, page: Page, state: SessionState) -> None:
        self._page = page
        self._pace = state.personality.pace

    async def delay(self, min_s: float, max_s: float) -> None:
        base = random.uniform(min_s, max_s)
        await asyncio.sleep(base * self._pace)

    async def type_text(self, text: str) -> None:
        for char in text:
            base_delay = random.randint(50, 180)
            await self._page.keyboard.type(char, delay=int(base_delay * self._pace))
            if random.random() < 0.02:
                await self.delay(0.5, 1.5)

    async def scroll(self, direction: str = "down", amount: int = 3) -> None:
        for _ in range(amount):
            delta = random.randint(200, 600)
            if direction == "up":
                delta = -delta
            await self._page.mouse.wheel(0, delta)
            await self.delay(0.3, 1.5)

    async def click(self, element: ElementHandle) -> None:
        box = await element.bounding_box()
        if not box:
            await element.click()
            return

        target_x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
        target_y = box["y"] + box["height"] * random.uniform(0.3, 0.7)

        steps = random.randint(8, 20)
        start_x = random.randint(100, 800)
        start_y = random.randint(100, 500)
        for i in range(steps):
            t = (i + 1) / steps
            cursor_x = start_x + (target_x - start_x) * t + random.randint(-3, 3)
            cursor_y = start_y + (target_y - start_y) * t + random.randint(-2, 2)
            await self._page.mouse.move(cursor_x, cursor_y)
            await asyncio.sleep(random.uniform(0.008, 0.035))

        await self._page.mouse.click(target_x, target_y)

    async def wiggle_mouse(self) -> None:
        await self._page.mouse.move(
            random.randint(300, 900), random.randint(200, 600),
        )

    async def scan_previews(self, duration_s: float = 10.0) -> None:
        elapsed = 0.0
        while elapsed < duration_s:
            await self._page.mouse.move(
                random.randint(200, 1000), random.randint(150, 600),
            )
            wait = random.uniform(1.0, 3.0)
            await self.delay(wait, wait)
            elapsed += wait
            if random.random() < 0.3:
                await self._page.mouse.wheel(0, random.randint(100, 300))
                await self.delay(0.5, 1.0)
                elapsed += 1.0
