import os
import time
import subprocess
import httpx
from io import BytesIO
from PIL import Image, ImageEnhance
from playwright.sync_api import sync_playwright
from transformers import pipeline
import pytesseract  # Не забудь: pip install pytesseract


class ProductSmartAnalyzer:
    def __init__(self):
        print("--- Загрузка локальной нейросети CLIP ---")
        self.classifier = pipeline(
            "zero-shot-image-classification",
            model="openai/clip-vit-base-patch32"
        )
        self.labels = [
            "front side of product packaging with logo",
            "back side of product packaging with ingredients and text",
            "lifestyle photo or advertisement",
            "small web icon"
        ]

    def open_site(self, url, p):
        """Твоя логика открытия через CDP."""
        subprocess.Popen([
            "chromium",
            "--remote-debugging-port=9222",
            "--user-data-dir=/tmp/playwright_session",
            "--start-maximized",
            "--force-device-scale-factor=1"
        ])
        time.sleep(3)
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        page.set_viewport_size({"width": 1920, "height": 1080})
        page.goto(url, wait_until="domcontentloaded")
        return browser, page

    def get_images_from_page(self, page):
        """Глубокий поиск картинок через JS (теперь он точно их видит)."""
        js_code = """
        () => {
            const urls = new Set();
            document.querySelectorAll('*').forEach(el => {
                ['src', 'data-src', 'srcset'].forEach(attr => {
                    let val = el.getAttribute(attr);
                    if (val) {
                        let url = val.split(',')[0].split(' ')[0].trim();
                        if (url.startsWith('http')) urls.add(url);
                    }
                });
            });
            return Array.from(urls);
        }
        """
        all_urls = page.evaluate(js_code)
        return [u for u in all_urls if not any(x in u.lower() for x in ['icon', 'svg', 'logo'])]

    def analyze_and_save(self, urls):
        """Анализ с умной проверкой тыла и сохранением переда."""
        results = {"front": None, "back": None}
        max_scores = {"front": 0.0, "back": 0.0}

        # Ключевые слова для характеристик (тыл)
        back_keywords = ['состав', 'изготовитель', 'характеристики', 'пищевая ценность', 'ingredients', 'nutrition']

        with httpx.Client() as client:
            # Берем больше кандидатов (до 25), чтобы точно найти оба фото
            for url in urls[:25]:
                try:
                    resp = client.get(url, timeout=10)
                    img = Image.open(BytesIO(resp.content)).convert("RGB")
                    if img.width < 250 or img.height < 250: continue

                    preds = self.classifier(img, candidate_labels=self.labels)
                    # Берем топ-2 предсказания, чтобы видеть альтернативу
                    top_pred = preds[0]
                    alt_pred = preds[1]

                    label = top_pred['label']
                    score = top_pred['score']

                    is_back_potential = "back side" in label

                    # --- ЛОГИКА ДЛЯ ТЫЛА (УЛУЧШЕННАЯ) ---
                    if is_back_potential:
                        # Повышаем контраст для OCR
                        enhanced_img = ImageEnhance.Contrast(img).enhance(1.8)
                        text = pytesseract.image_to_string(enhanced_img, lang='rus+eng').lower()

                        # Проверяем, есть ли текст характеристик
                        has_back_text = any(w in text for w in back_keywords) or len(text.split()) > 30

                        if has_back_text:
                            # Это точно тыл, сохраняем если скор выше
                            if score > max_scores["back"]:
                                max_scores["back"] = score
                                results["back"] = (img.copy(), url)
                                continue  # Успешно нашли тыл, идем к следующему фото
                        else:
                            # Нейросеть думала, что тыл, но текста нет.
                            # Скорее всего это ПЕРЕД или мусор. Даем шанс стать передом.
                            print(f"[ИНФО] CLIP пометил как ТЫЛ, но текста нет. Пробуем как ПЕРЕД для: {url[:40]}...")
                            is_back_potential = False  # Сбрасываем флаг тыла
                            # Если второе предсказание "front side", используем его скор
                            if "front side" in alt_pred['label']:
                                label = alt_pred['label']
                                score = alt_pred['score']

                    # --- ЛОГИКА ДЛЯ ЛИЦА ---
                    # Сюда попадают фото, которые CLIP сразу пометил как ПЕРЕД,
                    # ИЛИ те, которые не прошли проверку на текст ТЫЛА выше.
                    if "front side" in label and score > max_scores["front"]:
                        max_scores["front"] = score
                        results["front"] = (img.copy(), url)
                        print(f"[ИНФО] Нашел потенциальный ПЕРЕД: {url[:40]}... (score: {score:.2f})")

                except Exception as e:
                    # print(f"Ошибка обработки {url[:30]}: {e}")
                    continue

        # Сохранение результатов
        self.save_results(results)

    def save_results(self, results):
        """Метод сохранения (теперь он на месте)."""
        for side in ["front", "back"]:
            if results[side]:
                img, url = results[side]
                filename = f"detected_{side}.jpg"
                img.save(filename)
                print(f"[ГОТОВО] Сохранено {side} из {url}")
            else:
                print(f"[!] {side} сторона не найдена.")


def main():
    url = input("Ссылка на товар: ")
    analyzer = ProductSmartAnalyzer()
    with sync_playwright() as p:
        browser, page = analyzer.open_site(url, p)
        time.sleep(5)  # Ждем прогрузки
        image_urls = analyzer.get_images_from_page(page)
        analyzer.analyze_and_save(image_urls)
        browser.close()


if __name__ == "__main__":
    main()