# Test Writing Skill

## 목적
기능의 정확성을 보장하는 테스트 코드를 작성합니다.

## 원칙

1. **AAA 패턴**: Arrange(준비) → Act(실행) → Assert(검증)
2. **테스트 격리**: 각 테스트는 독립적으로 실행 가능해야 합니다
3. **명확한 이름**: `test_{기능}_{조건}_{기대결과}` 형식 사용
4. **경계값 테스트**: 정상, 경계, 예외 케이스를 모두 포함

## pytest 컨벤션

```python
class TestAgentFactory:
    """AgentFactory 단위 테스트."""

    async def test_create_agent_with_tools_resolves_all(self):
        """tool ref가 모두 해석되어 AgentInstance에 주입되는지 확인."""
        # Arrange
        ...
        # Act
        ...
        # Assert
        ...
```

## 커버리지 목표

- 핵심 비즈니스 로직: 90% 이상
- 유틸리티/헬퍼: 80% 이상
- 통합 테스트: 주요 흐름 커버
