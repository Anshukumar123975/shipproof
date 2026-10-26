from typing import Literal, Any
from pydantic import BaseModel, Field, model_validator
class Check(BaseModel):
    check_id: str = Field(max_length=100)
    requirement_id: str = Field(max_length=100)
    action_type: Literal['api','browser','interaction','unsupported']
    target: str = Field(default='/', max_length=2000)
    method: Literal['GET','HEAD','POST'] = 'GET'
    navigation_selector: str | None = None
    expected_path: str | None = None
    json_shape: dict[str, Literal['string','number','boolean','array','object','null']] = {}
    expected_status: int = Field(default=200, ge=100, le=599)
    json_path: str | None = None
    expected_value: Any = None
    selector: str | None = None
    text: str | None = None
    priority: Literal['critical','high','medium','low'] = 'high'
    estimated_cost: float = 1
    risk_level: str = 'read-only'
    preconditions: list[str] = []
    approval_requirement: bool = False
    expected_outcome: str = 'Explicit assertions match'
    @model_validator(mode='after')
    def safe(self):
        if self.action_type == 'interaction':
            self.approval_requirement = True
            self.method = 'POST'
            self.risk_level = 'disposable-interaction'
        elif self.method == 'POST':
            raise ValueError('Mutating methods require a supported approved interaction')
        if self.action_type == 'browser': self.method = 'GET'
        return self
class Requirement(BaseModel):
    id: str = Field(max_length=100)
    description: str = Field(max_length=4000)
    priority: Literal['critical','high','medium','low'] = 'high'
    paths: list[str] = []
    checks: list[Check] = Field(default_factory=list, max_length=30)
class Budget(BaseModel):
    actions: int = Field(default=20, ge=1, le=100)
    browser: int = Field(default=10, ge=0, le=50)
    api: int = Field(default=20, ge=0, le=100)
    llm_calls: int = Field(default=8, ge=0, le=20)
    llm_tokens: int = Field(default=32000, ge=0, le=100000)
    llm_cost: float = Field(default=1, ge=0, le=10)
    active_seconds: int = Field(default=180, ge=1, le=600)
    deadline_seconds: int = Field(default=1200, ge=30, le=3600)
class CreateRun(BaseModel):
    scenario: str = 'verified'
    pr_url: str | None = None
    staging_url: str = 'http://demo:8001'
    authorized: bool = False
    requirements: list[Requirement] = Field(default_factory=list, max_length=40)
    natural_requirements: str = Field(default='', max_length=12000)
    policy: Literal['fixed','stagnation','progress'] = 'progress'
    budget: Budget = Field(default_factory=Budget)
    ai: bool = False
class AIAdvice(BaseModel):
    summary: str
    requirements: list[Requirement] = []
    risks: list[str] = []
