# Промпт 31. Качество Tesseract на табличных РСБУ-формах

## Цель

Поднять надёжность распознавания цифр на сканированных балансовых
формах. Симптомы из живого `run_id=13`:

- `CHMF-3-1893787` (H1 2025, scan-only, 16 pages, pure-OCR через
  Tesseract): coverage=0.75 (revenue + net_income есть,
  total_assets и total_debt — null).
- `CHMF-3-1902512` (9M 2025) — то же самое для comparative-prior
  периода.
- В целом OCR @ 300 DPI с `--psm 3` (auto layout) на тонких
  grid-таблицах форм 0710001/0710002 регулярно путает `8↔3`,
  `0↔O`, теряет цифры из колонок «На 31 декабря 2024 г.» (правые
  колонки страдают сильнее левых из-за смещения header'а).

Patch 31 добавляет три рычага:

1. Поднять default DPI с 300 → 400.
2. Включить `--psm 6` (single uniform block of text) — он лучше
   ведёт себя на табличных формах, чем default `--psm 3`.
3. Per-page retry: если первый проход на странице вернул < 80
   non-whitespace символов или digit-ratio < 5% — повторить с
   `--psm 4` (single column of variable-sized text). Это страховка
   от страниц-обложек, у которых `--psm 6` ломает чтение.

Пункты 1 и 2 — низкорисковые конфигурационные изменения. Пункт 3 —
opt-in, под флагом, чтобы не удваивать время OCR на эмитентах,
которые и так нормально читаются.

## Контекст

- Tesseract на CPU 2-vCPU VPS — ~1.5–2 секунды на страницу при 300
  DPI; при 400 DPI ~3 секунды. Прогон 50 эмитентов с retry — +5–8
  минут к общему времени. Приемлемо для cron-job.
- **Зависимости:** независим от других патчей серии. Эффект виден
  на любом scan-only / hybrid PDF (Patch 18 всё ещё route'ит scan-страницы
  через Tesseract, как и pure-scan документы).
- Заметка про cloud-OCR: `config/ocr.yaml` имеет stub'ы для Yandex
  Vision / Google Vision. Patch 31 их не активирует — это отдельная
  задача (см. §10 README, future work).

## Задачи

### 1. `src/edx/stages/text_extractor/ocr/tesseract.py`

Текущая реализация:

```python
class TesseractOCRProvider:
    def __init__(self, *, dpi: int = 300) -> None:
        self.dpi = dpi
    def recognize(self, pdf_path, langs):
        images = convert_from_path(str(pdf_path), dpi=self.dpi)
        lang_arg = "+".join(langs) if langs else "eng"
        pages = []
        for index, image in enumerate(images):
            text = pytesseract.image_to_string(image, lang=lang_arg) or ""
            pages.append(PageText(page_number=index + 1, text=text))
        return pages
```

Расширить:

```python
class TesseractOCRProvider:
    def __init__(
        self,
        *,
        dpi: int = 400,
        psm: int = 6,
        retry_psm: int | None = 4,
        retry_min_chars: int = 80,
        retry_min_digit_ratio: float = 0.05,
    ) -> None:
        self.dpi = dpi
        self.psm = psm
        self.retry_psm = retry_psm
        self.retry_min_chars = retry_min_chars
        self.retry_min_digit_ratio = retry_min_digit_ratio
        self._log = get_logger("edx.stages.text_extractor.ocr.tesseract")

    def recognize(self, pdf_path, langs):
        if shutil.which("tesseract") is None:
            raise TesseractOCRMissingError(...)
        images = convert_from_path(str(pdf_path), dpi=self.dpi)
        lang_arg = "+".join(langs) if langs else "eng"
        pages = []
        for index, image in enumerate(images):
            text = self._run_once(image, lang_arg, self.psm)
            if self.retry_psm is not None and self._needs_retry(text):
                retry_text = self._run_once(image, lang_arg, self.retry_psm)
                if self._is_better(retry_text, text):
                    self._log.info(
                        "tesseract_retry_won",
                        page=index + 1,
                        primary_psm=self.psm,
                        retry_psm=self.retry_psm,
                        primary_chars=len(text.strip()),
                        retry_chars=len(retry_text.strip()),
                    )
                    text = retry_text
            pages.append(PageText(page_number=index + 1, text=text))
        return pages

    def _run_once(self, image, lang_arg, psm):
        config = f"--psm {psm}"
        return pytesseract.image_to_string(image, lang=lang_arg, config=config) or ""

    def _needs_retry(self, text):
        stripped = text.strip()
        if len(stripped) < self.retry_min_chars:
            return True
        digit_count = sum(1 for c in stripped if c.isdigit())
        if digit_count / max(len(stripped), 1) < self.retry_min_digit_ratio:
            return True
        return False

    def _is_better(self, candidate, baseline):
        return len(candidate.strip()) > len(baseline.strip())
```

Импорт `from edx.logging_setup import get_logger`.

### 2. `src/edx/config/ocr_config.py`

Расширить `OCRConfig` (или класс, в котором уже живёт `engine` и
`tesseract_dpi`):

```python
tesseract_dpi: int = Field(default=400, ge=72, le=1200)
tesseract_psm: int = Field(default=6, ge=0, le=13)
tesseract_retry_psm: int | None = Field(default=4)
tesseract_retry_min_chars: int = Field(default=80, ge=0)
tesseract_retry_min_digit_ratio: float = Field(default=0.05, ge=0.0, le=1.0)
```

### 3. `src/edx/stages/text_extractor/ocr/factory.py`

В фабрику Tesseract-провайдера прокинуть все 5 новых полей.

### 4. `config/ocr.yaml`

Расширить с комментариями:

```yaml
engine: tesseract

# Tesseract DPI: на банковских РСБУ-формах 300 даёт путаницу 8↔3
# на правых колонках; 400 заметно надёжнее ценой ~+50% CPU/page.
tesseract_dpi: 400

# Tesseract PSM (page segmentation mode):
# 3 = auto (default Tesseract; плохо на табличных формах);
# 6 = single uniform block of text (рекомендуется для РСБУ-форм);
# 4 = single column of variable-sized text (хорошо для cover-страниц).
tesseract_psm: 6

# Retry: если страница вернула < min_chars символов или digit_ratio
# < min_digit_ratio, повторить с retry_psm. null отключает retry.
tesseract_retry_psm: 4
tesseract_retry_min_chars: 80
tesseract_retry_min_digit_ratio: 0.05
```

### 5. Тесты

Создать `tests/stages/text_extractor/ocr/test_tesseract_quality.py`:

- `test_default_construction_uses_psm_6_dpi_400`: проверить, что
  `TesseractOCRProvider()` без аргументов имеет
  `dpi=400, psm=6, retry_psm=4`.
- `test_run_once_passes_psm_to_pytesseract`: monkey-patch
  `pytesseract.image_to_string` на mock, проверить, что
  `config="--psm 6"` передан.
- `test_needs_retry_short_text_triggers`: text=20 chars → True.
- `test_needs_retry_low_digit_ratio_triggers`: text="русский
  текст без цифр совсем нет" → True.
- `test_needs_retry_passing_text_does_not_trigger`: text=200 chars
  с 30 цифрами → False.
- `test_retry_chosen_when_better`: первый pass возвращает 50
  chars, retry возвращает 200 chars → итог = retry-output.
- `test_retry_not_chosen_when_not_better`: первый pass = 50, retry
  = 30 → остаётся первый.
- `test_retry_disabled_when_retry_psm_is_none`: с
  `retry_psm=None`, даже плохой первый pass не вызывает retry.

Существующий `tests/stages/text_extractor/ocr/test_tesseract.py`
(если он есть) обновить — там фикстура может проверять старый
`dpi=300`. Поправить ожидания.

### 6. `USER_GUIDE.md`

В разделе «Какие YAML можно править» строку про `config/ocr.yaml`
дополнить: `tesseract_psm` (3/6/4), `tesseract_retry_*`.

В разделе «Если что-то не работает» добавить:

> **OCR пропускает цифры в правых колонках балансовой формы** —
> поднять `tesseract_dpi` до 600. Если после этого OCR работает, но
> прогон затягивается — оставить 400 и подключить cloud-OCR (см.
> «Расширение»).

В блоке «Стоимость в эксплуатации» обновить оценку CPU при 400 DPI
(+50% к текущему).

### 7. `PIPELINE_LOGIC.md`

В §4.1 («Где включается OCR») обновить параметры по умолчанию: DPI
400, PSM 6, retry с PSM 4. Описать retry-цикл.

## Тесты, которые должны проходить

- Все 8 новых тестов зелёные.
- Существующий `tests/stages/text_extractor/test_service.py`
  (hybrid OCR паттерн) не сломан — Tesseract-провайдер инжектится
  через DI, поэтому достаточно обновить фикстуры конструкторов.
- `make lint typecheck test` зелёный.

## Definition of Done

- В `config/ocr.yaml` новые knobs закоммичены с комментариями.
- На странице с балансовой формой РСБУ (фикстура: 1 страница из
  `FS_31122025.pdf` стр. 8 — оператор кладёт под
  `tests/fixtures/pdf/chmf_rsbu_balance_page.pdf`, или, если
  оператор не положил — тест помечается `pytest.skip`) Tesseract
  возвращает text с ≥ 20 числовыми токенами длиной 6+ цифр (для
  балансовых сумм типа `620 099 738`).
- В `pipeline.log` событие `tesseract_retry_won` появляется на
  cover-страницах с малым количеством текста, где `--psm 4` лучше
  читает.
- Время `text_extractor` стадии вырастает в пределах +50% (контроль
  через `publication_extracted` log event'ы — поле `total_chars`
  остаётся в том же порядке, а wall-clock между двумя соседними
  событиями увеличивается).
- USER_GUIDE и PIPELINE_LOGIC обновлены.
