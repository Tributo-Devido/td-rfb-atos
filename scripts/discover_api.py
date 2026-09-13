"""
discover_api.py — Descobre endpoints da API do normasinternet2 via Playwright.

Abre o portal, navega para SCs de exemplo e intercepta todas as requests
Fetch/XHR. Imprime URL, método, status, content-type e tamanho da response.
"""
from __future__ import annotations

import json
import sys
import time

from playwright.sync_api import sync_playwright

ATOS_TESTE = [
    # com PDF
    ("150754", ["", "/visao/original", "/visao/vigente", "/visao/relacional", "/visao/multivigente"]),
    # sem PDF
    ("150771", ["/visao/relacional"]),
]

BASE = "https://normasinternet2.receita.fazenda.gov.br/#/consulta/externa"


def main():
    capturas: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        page = context.new_page()

        def on_response(response):
            try:
                ct = response.headers.get("content-type", "")
                # Só captura JSON / API / dados (não HTML/JS/CSS/IMG)
                url = response.url
                if any(skip in url for skip in [".js", ".css", ".png", ".jpg", ".svg", ".ico", ".woff", ".ttf"]):
                    return
                if "application/json" in ct or "/api/" in url or "/sijut" in url.lower() or "/normas" in url.lower():
                    body = ""
                    try:
                        if "json" in ct:
                            body = json.dumps(response.json())[:500]
                        elif response.body() and len(response.body()) < 5000:
                            body = response.body().decode("utf-8", errors="replace")[:500]
                    except Exception:
                        body = "<unable to read>"
                    capturas.append({
                        "method": response.request.method,
                        "url": url,
                        "status": response.status,
                        "content_type": ct,
                        "body_preview": body,
                    })
                    print(f"[{response.status}] {response.request.method} {url}")
                    print(f"  ct: {ct}")
                    if body:
                        print(f"  body: {body[:200]}")
                    print()
            except Exception as e:
                print(f"erro on_response: {e}", file=sys.stderr)

        page.on("response", on_response)

        for ato_id, visoes in ATOS_TESTE:
            for visao in visoes:
                url = f"{BASE}/{ato_id}{visao}"
                print(f"\n========== Navegando: {url} ==========\n")
                try:
                    page.goto(url, timeout=60000, wait_until="networkidle")
                    time.sleep(2)
                except Exception as e:
                    print(f"erro nav: {e}")

        browser.close()

    print("\n=== RESUMO ===")
    print(f"{len(capturas)} requests capturadas")
    # Salva pra inspeção
    with open("api_discovery.json", "w", encoding="utf-8") as f:
        json.dump(capturas, f, indent=2, ensure_ascii=False)
    print("Salvo em scripts/api_discovery.json")


if __name__ == "__main__":
    main()
