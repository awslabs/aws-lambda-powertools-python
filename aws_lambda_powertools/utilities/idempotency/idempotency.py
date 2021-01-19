"""
Primary interface for idempotent Lambda functions utility
"""
import logging
from typing import Any, Callable, Dict

from aws_lambda_powertools.middleware_factory import lambda_handler_decorator
from aws_lambda_powertools.utilities.idempotency.persistence.base import STATUS_CONSTANTS, BasePersistenceLayer

from ..typing import LambdaContext
from .exceptions import AlreadyInProgressError, ItemAlreadyExistsError, ItemNotFoundError

logger = logging.getLogger(__name__)


def default_error_callback():
    raise


@lambda_handler_decorator
def idempotent(
    handler: Callable[[Any, LambdaContext], Any],
    event: Dict[str, Any],
    context: LambdaContext,
    persistence_store: BasePersistenceLayer,
) -> Any:
    """
    Middleware to handle idempotency

    Parameters
    ----------
    handler: Callable
        Lambda's handler
    event: Dict
        Lambda's Event
    context: Dict
        Lambda's Context
    persistence_store: BasePersistenceLayer
        Instance of BasePersistenceLayer to store data

    Examples
    --------
    **Processes Lambda's event in an idempotent manner**
        >>> from aws_lambda_powertools.utilities.idempotency import idempotent, DynamoDBPersistenceLayer
        >>>
        >>> persistence_store = DynamoDBPersistenceLayer(event_key="body", table_name="idempotency_store")
        >>>
        >>> @idempotent(persistence_store=persistence_store)
        >>> def handler(event, context):
        >>>     return {"StatusCode": 200}
    """

    try:
        # We call save_inprogress first as an optimization for the most common case where no idempotent record already
        # exists. If it succeeds, there's no need to call get_record.
        persistence_store.save_inprogress(event=event)
    except ItemAlreadyExistsError:
        try:
            event_record = persistence_store.get_record(event)
        except ItemNotFoundError:
            return _call_lambda(handler=handler, persistence_store=persistence_store, event=event, context=context)

        if event_record.status == STATUS_CONSTANTS["EXPIRED"]:
            return _call_lambda(handler=handler, persistence_store=persistence_store, event=event, context=context)

        if event_record.status == STATUS_CONSTANTS["INPROGRESS"]:
            raise AlreadyInProgressError(
                f"Execution already in progress with idempotency key: "
                f"{persistence_store.event_key}={event_record.idempotency_key}"
            )

        if event_record.status == STATUS_CONSTANTS["COMPLETED"]:
            return event_record.response_json_as_dict()

    return _call_lambda(handler=handler, persistence_store=persistence_store, event=event, context=context)


def _call_lambda(
    handler: Callable, persistence_store: BasePersistenceLayer, event: Dict[str, Any], context: LambdaContext
) -> Any:
    """

    Parameters
    ----------
    handler: Callable
        Lambda handler
    persistence_store: BasePersistenceLayer
        Instance of persistence layer
    event
        Lambda event
    context
        Lambda context
    """
    try:
        handler_response = handler(event, context)
    except Exception as ex:
        persistence_store.save_error(event=event, exception=ex)
        raise
    else:
        persistence_store.save_success(event=event, result=handler_response)
    return handler_response