# Промпт 08. LLM-провайдер с двухканальным доступом

## Цель
Реализовать абстракцию LLM с приоритетным каналом — прямой Anthropic API — и автоматическим fallback на OpenRouter при недоступности или отсутствии ключа. Поддержать строгую JSON-схему ответа и нативный PDF-input для Anthropic.

## Контекст из ТЗ
- Раздел 7.1, п.6 и п.7: Metric/Event Extractor получают «структурированный JSON ... строгая JSON-схема ответа».
- Раздел 8: модель — `claude-sonnet-4-6` через Anthropic API; OpenRouter — fallback.
- Раздел 17, п.2: «двухканальный — прямой Anthropic API (приоритет, даёт нативный PDF-input) с автоматическим fallback на OpenRouter».
- Раздел 12.2: идемпотентность — повторно не вызывать LLM для уже обработанной публикации.

## Задачи
1. Добавить зависимости: `anthropic` (официальный SDK), `httpx` (уже есть для OpenRouter).
2. Создать `src/edx/providers/llm/`:
   - `base.py`:
     ```python
     class LLMRequest(BaseModel):
         system: str
         user_text: str
         pdf_bytes: bytes | None = None  # для нативного PDF
         json_schema: dict
         max_tokens: int
         temperature: float
     class LLMResponse(BaseModel):
         data: dict          # распарсенный JSON
         raw_text: str       # сырой ответ модели (для логов)
         provider: str
         model: str
         input_tokens: int
         output_tokens: int
     class LLMProvider(Protocol):
         name: str
         supports_pdf_input: bool
         async def complete(self, req: LLMRequest) -> LLMResponse: ...
     class LLMUnavailableError(RuntimeError): ...
     ```
   - `anthropic_provider.py`:
     - Использует Anthropic SDK с `tools`/`response_format` для строгой JSON-схемы (через tool-use с одним инструментом, чьим input-schema является `req.json_schema`). Если `pdf_bytes` задан — передаёт как `document` content block.
     - Включить prompt caching на больших system-промптах (TTL 5 мин), если возможно.
     - При 401/403/«no credits» / отсутствии ключа — выбросить `LLMUnavailableError`.
   - `openrouter_provider.py`:
     - HTTP-клиент к `https://openrouter.ai/api/v1/chat/completions`.
     - `supports_pdf_input = False` (если PDF задан — конвертировать в текстовый input заранее на стадии extractor — об этом ниже).
     - Использует тот же `json_schema` через OpenRouter'овский `response_format: {type: "json_schema", json_schema: {...}}`. Если модель этого не поддерживает — instruct-промпт с явной схемой и парсинг JSON из текста с jsonrepair как fallback.
   - `chain.py`:
     - `class FallbackChain(LLMProvider)` принимает `[primary, fallback]`. Метод `complete` пробует первый; на `LLMUnavailableError` или `httpx.TransportError` — переключается на второй и логирует структурированное событие `llm_fallback`.
   - `factory.py`:
     - Собирает цепочку из `LLMConfig` и `Secrets`. Если ключ Anthropic пуст — primary не создаётся, цепочка состоит из одного OpenRouter (соответствует ТЗ: «прямой Anthropic API (приоритет, если ключ задан) + OpenRouter (fallback)»).
3. Ретраи внутри каждого провайдера — через `tenacity` (экспоненциальный бэкофф, не больше `llm.max_retries`). Транспортные ошибки и 5xx — ретраят; 4xx, кроме 408/429, — не ретраят, но конвертируют в `LLMUnavailableError`, чтобы цепочка переключилась на fallback.
4. Кеширование на уровне provider'а: вычислять SHA-256 от `(system + user_text + pdf_hash + json_schema_hash)` и хранить ответ в `data/processed/_llm_cache/{hash}.json`. На попадание — возвращать без вызова сети. Очистка — отдельной CLI-командой `edx cache prune --older-than 30d`. Это покрывает требование «не делать повторных вызовов LLM» из раздела 12.2.
5. Структурно логировать каждый вызов: провайдер, модель, токены, время, статус (`hit_cache` / `success` / `fallback` / `failed`).

## Тесты, которые должны проходить
- Юнит-тесты обоих провайдеров с моками SDK / HTTP:
  - успешный вызов возвращает распарсенный JSON;
  - ошибка 401 у Anthropic → `LLMUnavailableError`;
  - ошибка 5xx у OpenRouter с ретраями: 2 неудачи + успех = успех с двумя ретраями;
  - ответ без валидного JSON у OpenRouter → запасной репейр через jsonrepair (или `json5`); если и это не помогает — `LLMUnavailableError`.
- Юнит-тесты `FallbackChain`:
  - Anthropic недоступен → вызывается OpenRouter; в логе есть событие `llm_fallback`.
  - оба недоступны → `LLMUnavailableError` пробрасывается наверх.
- Юнит-тест кеша: одинаковый запрос второй раз не вызывает провайдер.
- Никаких реальных сетевых вызовов в `pytest`.

## Definition of Done
- Цепочка собирается из конфига + `.env`. Если оба ключа пустые — `factory` возвращает явную ошибку при старте `edx update` с подсказкой, какие переменные нужно заполнить.
- Ни одна стадия LLM-извлечения не знает о провайдерах — только об интерфейсе `LLMProvider`.
- Кеш-каталог в `.gitignore`.
