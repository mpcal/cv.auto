from alembic import context
from sqlalchemy import create_engine, event

from cvopt.adapters.db.engine import _set_pragmas
from cvopt.adapters.db.schema import metadata

url = context.config.get_main_option("sqlalchemy.url")
assert url is not None

if context.is_offline_mode():
    context.configure(url=url, target_metadata=metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _set_pragmas)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
