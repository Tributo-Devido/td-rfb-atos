"""
find_pdf_endpoint.py — Captura URL do PDF clicando no anchor "Baixar o arquivo".
"""
from __future__ import annotations

import time
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    pdf_requests = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            accept_downloads=True,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        page = context.new_page()

        page.on("request", lambda r: pdf_requests.append((r.method, r.url, r.resource_type, dict(r.headers))))

        url = "https://normasinternet2.receita.fazenda.gov.br/#/consulta/externa/150754"
        print(f"Abrindo {url}")
        page.goto(url, timeout=60000, wait_until="networkidle")
        time.sleep(3)

        # Procurar anchor com title "Baixar o arquivo"
        print("\n=== Procurando anchor de download ===")
        anchors = page.locator("a[title='Baixar o arquivo']").all()
        print(f"  Encontrados: {len(anchors)}")
        for i, a in enumerate(anchors):
            href = a.get_attribute("href")
            txt = a.inner_text()
            print(f"  [{i}] href={href}, text={txt}")

        if anchors:
            print("\nClicando...")
            try:
                with page.expect_download(timeout=15000) as dl_info:
                    anchors[0].click()
                download = dl_info.value
                save_dir = Path(__file__).resolve().parent
                save_path = save_dir / f"sample_{download.suggested_filename}"
                download.save_as(save_path)
                print(f"  PDF salvo: {save_path} ({save_path.stat().st_size} bytes)")
                print(f"  URL do download: {download.url}")
            except Exception as e:
                print(f"  erro click: {e}")

        time.sleep(1)
        browser.close()

    print("\n=== Requests interessantes (PDF/binary) ===")
    for method, url, rtype, headers in pdf_requests:
        if any(k in url.lower() for k in ["pdf", "anexo", "binario", "arquivo", "download", "imprimir"]):
            print(f"  [{method}] [{rtype}] {url}")
            for k, v in headers.items():
                if k.lower() in ("accept", "referer"):
                    print(f"    {k}: {v[:80]}")


if __name__ == "__main__":
    main()
