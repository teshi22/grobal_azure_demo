import asyncio

from app.services.cosmos import TravelRequestStore


class _AsyncItems:
    def __init__(self, items: list[dict]):
        self._items = iter(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._items)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _Container:
    def __init__(self):
        self.query_kwargs: dict | None = None

    def query_items(self, **kwargs):
        self.query_kwargs = kwargs
        return _AsyncItems([{"request_id": "TR-1"}])


def test_list_by_user_uses_async_cosmos_query_without_sync_only_options():
    container = _Container()
    store = TravelRequestStore.__new__(TravelRequestStore)
    store._container = container

    items = asyncio.run(store.list_by_user("user-1", limit=25))

    assert items == [{"request_id": "TR-1"}]
    assert container.query_kwargs == {
        "query": (
            "SELECT TOP @limit * FROM c WHERE c.user_id = @user_id "
            "ORDER BY c.submitted_at DESC"
        ),
        "parameters": [
            {"name": "@limit", "value": 25},
            {"name": "@user_id", "value": "user-1"},
        ],
    }
