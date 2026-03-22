from __future__ import annotations

import aiohttp


class CryptoPayClient:
    def __init__(self, token: str):
        self.token = token.strip()
        self.base_url = "https://pay.crypt.bot/api"

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    async def _call(self, method: str, payload: dict) -> dict:
        if not self.enabled:
            raise RuntimeError("CRYPTO_BOT_TOKEN is not configured")
        headers = {"Crypto-Pay-API-Token": self.token}
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.base_url}/{method}", headers=headers, json=payload, timeout=25) as resp:
                data = await resp.json(content_type=None)
                if not data.get("ok"):
                    raise RuntimeError(f"Crypto API error: {data}")
                return data["result"]

    async def create_invoice(self, amount_usdt: float, payload: str, description: str) -> dict:
        return await self._call(
            "createInvoice",
            {"asset": "USDT", "amount": str(amount_usdt), "payload": payload, "description": description},
        )

    async def get_invoice(self, invoice_id: str | int) -> dict:
        result = await self._call("getInvoices", {"invoice_ids": str(invoice_id)})
        items = result.get("items", [])
        if not items:
            raise RuntimeError("Invoice not found")
        return items[0]
