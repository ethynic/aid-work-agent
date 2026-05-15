## ADDED Requirements

### Requirement: Pending clarification state stored in Redis
The system SHALL persist pending subagent clarification contexts to Redis instead of process memory.

#### Scenario: User replies to a clarification request across workers
- **WHEN** a subagent requests clarification and the context is stored in Redis under key `pending_clarification:{session_id}`
- **THEN** a subsequent request from the same session handled by a different worker SHALL retrieve the clarification context from Redis
- **AND** the system SHALL recognize the user reply as a clarification response
- **AND** after processing the clarification, the Redis key SHALL be deleted

#### Scenario: Clarification expires
- **WHEN** a clarification context is stored in Redis with a TTL of 3600 seconds
- **THEN** if the user does not reply within the TTL, the Redis key SHALL expire automatically
- **AND** subsequent requests from that session SHALL be treated as new messages, not clarification replies

### Requirement: Session cancellation stored in Redis
The system SHALL store session cancellation flags in Redis instead of process memory.

#### Scenario: User clicks stop generation across workers
- **WHEN** user clicks "stop generation" for a session and the cancellation flag is stored in Redis under key `cancelled_session:{session_id}` with a TTL of 300 seconds
- **THEN** the agent thread running in any worker SHALL check Redis for the cancellation flag during generation
- **AND** if the flag exists, the agent SHALL stop generating and clean up
- **AND** after generation completes or is cancelled, the flag SHALL be removed from Redis

### Requirement: Uploaded file metadata stored in Redis
The system SHALL store uploaded file metadata in Redis instead of process memory.

#### Scenario: File uploaded and read across workers
- **WHEN** a file is uploaded and its metadata is stored in Redis under key `uploaded_file:{file_id}` with a TTL of 86400 seconds
- **THEN** any worker processing a subsequent request referencing that `file_id` SHALL retrieve the metadata from Redis
- **AND** the metadata SHALL include at minimum: file path, original name, MIME type, and upload timestamp

### Requirement: Subagent task records stored in Redis
The system SHALL persist subagent task records to Redis instead of process memory.

#### Scenario: Query task status across workers
- **WHEN** a subagent task is delegated and its record is stored in Redis under key `task_record:{execution_id}` with a TTL of 7200 seconds
- **THEN** any worker querying the task status by `execution_id` SHALL retrieve the record from Redis
- **AND** task status updates SHALL be written back to Redis atomically

### Requirement: Execution plans stored in Redis
The system SHALL persist execution plans to Redis instead of process memory.

#### Scenario: Multi-step plan execution across workers
- **WHEN** an execution plan is created and stored in Redis under key `execution_plan:{session_id}` with a TTL of 3600 seconds
- **THEN** any worker retrieving the plan for that session SHALL load it from Redis
- **AND** task status updates within the plan SHALL be written back to Redis
- **AND** the plan SHALL be serialized as JSON for storage

### Requirement: Unified Redis client interface
The system SHALL provide a unified Redis client that abstracts key naming, serialization, and TTL management.

#### Scenario: Developer uses Redis client for new feature
- **WHEN** a developer needs to store cross-request state for a new feature
- **THEN** they use the `RedisClient` class from `src/core/redis_client.py`
- **AND** the client handles JSON serialization/deserialization automatically
- **AND** the client supports setting and checking TTL
- **AND** the client logs warnings and falls back to in-memory storage if Redis is unavailable
