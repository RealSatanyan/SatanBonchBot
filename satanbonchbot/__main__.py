"""Точка входа пакета: ``python -m satanbonchbot``.

Запускает событийный цикл бота. Вся прикладная логика — в
``satanbonchbot.main`` (исторический модуль-оркестратор и фасад-реэкспорт).
"""
import asyncio

from satanbonchbot.main import main


if __name__ == "__main__":
    asyncio.run(main())
