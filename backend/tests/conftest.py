import os
os.environ['DATABASE_URL']='sqlite:////tmp/shipproof-unit.db'
os.environ['ARTIFACT_ROOT']='/tmp/shipproof-test-artifacts'
os.environ['OPENROUTER_API_KEY']=''
import pytest
from app.db import Base,engine
@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(engine); Base.metadata.create_all(engine)
    yield
