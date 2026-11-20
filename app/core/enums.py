from enum import Enum


class UserRole(str, Enum):
    ADMIN = "ROLE_ADMIN"
    ENGINEER = "ROLE_ENGINEER"
    ONCALL = "ROLE_ONCALL"
    USER = "ROLE_USER"


class IncidentCategory(str, Enum):
    DATABASE = "DATABASE"
    CACHE = "CACHE"
    API = "API"
    NETWORK = "NETWORK"
    DEPLOYMENT = "DEPLOYMENT"
    RESOURCE = "RESOURCE"
    SECURITY = "SECURITY"
    MIDDLEWARE = "MIDDLEWARE"
    UNKNOWN = "UNKNOWN"


class IncidentSeverity(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class IncidentStatus(str, Enum):
    NEW = "NEW"
    TRIAGED = "TRIAGED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    INVESTIGATING = "INVESTIGATING"
    MITIGATED = "MITIGATED"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class IncidentEnvironment(str, Enum):
    PRODUCTION = "PRODUCTION"
    STAGING = "STAGING"
    TEST = "TEST"
    DEVELOPMENT = "DEVELOPMENT"
    UNKNOWN = "UNKNOWN"


class IncidentSource(str, Enum):
    MANUAL = "MANUAL"
    API = "API"
    WEBHOOK = "WEBHOOK"
    MONITORING = "MONITORING"


class IncidentNoteType(str, Enum):
    COMMENT = "COMMENT"
    INVESTIGATION = "INVESTIGATION"
    MITIGATION = "MITIGATION"
    RESOLUTION = "RESOLUTION"
    SYSTEM = "SYSTEM"


class KnowledgeSourceStatus(str, Enum):
    ACTIVE = "ACTIVE"
    STALE = "STALE"
    ARCHIVED = "ARCHIVED"
