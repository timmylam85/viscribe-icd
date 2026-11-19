from alembic import context

from viscribe_api.config import get_settings
from viscribe_api.db import make_session_factory
from viscribe_api.models import Base

if context.is_offline_mode():
    context.configure(dialect_name="postgresql", target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = make_session_factory(get_settings()).kw["bind"]
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
