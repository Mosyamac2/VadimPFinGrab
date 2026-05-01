# Промпт 07. Text Extractor (нативный + OCR)

## Цель
Извлечь текстовый слой из машиночитаемых PDF и распознать сканы через Tesseract. Подготовить очищенный текст и (где разумно) таблицы для последующей передачи в LLM.

## Контекст из ТЗ
- Раздел 7.1, п.5.
- Раздел 8: `pdfplumber` — для таблиц, `pymupdf` — для текста и скорости; Tesseract `rus+eng` локально, опциональный облачный OCR через конфиг.
- Раздел 16: качество скана может потребовать перехода на облачный OCR — заложено абстракцией.

## Задачи
1. Добавить зависимости: `pdfplumber`, `pytesseract`, `Pillow`. В README прописать системные требования: `apt-get install tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng poppler-utils`.
2. Создать `src/edx/stages/text_extractor/`:
   - `native.py`:
     - `extract_text(pdf_path) -> list[PageText]` через pymupdf (быстро, чисто текстом).
     - `extract_tables(pdf_path) -> list[PageTables]` через pdfplumber (страницы → списки таблиц → списки строк → списки ячеек). Это используется как дополнительный сигнал для LLM, не обязателен.
   - `ocr/` — пакет с интерфейсом и реализациями:
     - `base.py`: `class OCRProvider(Protocol): def recognize(self, pdf_path: Path, langs: list[str]) -> list[PageText]`.
     - `tesseract.py`: реализация через `pytesseract` + `pdf2image` (рендерит страницы в PNG, скармливает Tesseract). DPI — из конфига `ocr.tesseract_dpi`, дефолт `300`.
     - `yandex_vision.py` и `google_vision.py` — **заглушки** с `NotImplementedError("planned, see config/ocr.yaml")`. Это исполняет п. ТЗ 8 «опциональный, облачный» без реального кода — но с готовым местом для подключения.
     - `factory.py`: `build_ocr_provider(ocr_config) -> OCRProvider`.
   - `service.py`:
     - `TextExtractorService.run(publication)`.
     - Для каждого `documents.is_machine_readable = 1` — нативное извлечение, для `= 0` — OCR.
     - Результат пишется в `data/processed/{ticker}/{publication_id}/{document_id}.json` со структурой `{pages: [{page_number, text, tables?}], extraction_method: "native"|"ocr_tesseract"|..., extracted_at}`.
     - В БД: расширить миграцию (`0003_text_extracts.sql`) добавить колонку `documents.text_extract_path TEXT` — сохранять туда путь к JSON.
     - При успехе — публикация переходит в статус `extracted`.
3. Чистка текста: единая функция `normalize_text` — схлопывание whitespace, замена «мягких» переносов, нормализация пробелов вокруг знаков, обрезка хедеров/футеров (если они идентичны на N страницах — удалить).
4. Лимит на длину: если документ > `text_extractor.max_chars` (дефолт `400 000` символов) — обрезать с warning; для LLM-стадии в любом случае дальше будет своё разбиение.

## Тесты, которые должны проходить
- Юнит-тест `native.extract_text` на сгенерированном PDF с известным текстом — точное совпадение.
- Юнит-тест `normalize_text` — фиксированные кейсы.
- Юнит-тест Tesseract: тест помечается `pytest.mark.requires_tesseract`, скип, если бинарник не найден; при наличии — на простом синтетическом PNG с известной строкой возвращает её.
- Юнит-тест сервиса:
  - смешанная публикация (один machine-readable PDF + один скан) — на выходе два JSON, корректные `extraction_method`, путь записан в `documents.text_extract_path`.
- Все облачные OCR-провайдеры — `NotImplementedError`, в тесте поднимается `pytest.raises`.

## Definition of Done
- Стадия читает только PDF из `documents`, пишет JSON в `data/processed/`, обновляет `documents.text_extract_path`, переводит публикацию в `extracted`.
- Подмена OCR-провайдера через `ocr.yaml` не требует правки кода стадии.
- Тяжёлый OCR — отдельная зависимость, отсутствие Tesseract не ломает остальные тесты.
