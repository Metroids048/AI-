"""Signal ensemble and meta-label API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from apps.api.http import api_error, collection_response, not_found
from services.database import get_db_session
from services.strategy_library import ExecutionRepository
from services.strategy_library.ensemble import SignalEnsembleService
from shared.models import (
    CollectionResponse,
    MetaLabel,
    MetaLabelRequest,
    SignalEnsemble,
    SignalEnsembleRequest,
)

router = APIRouter(tags=["signal-ensemble"])


def _repo(db: Session) -> ExecutionRepository:
    return ExecutionRepository(db)


@router.get("/ensembles", response_model=CollectionResponse[SignalEnsemble])
def list_ensembles(db: Session = Depends(get_db_session)) -> CollectionResponse[SignalEnsemble]:
    return collection_response(_repo(db).list_signal_ensembles())


@router.post("/ensembles", response_model=SignalEnsemble, status_code=status.HTTP_201_CREATED)
def create_ensemble(body: SignalEnsembleRequest, db: Session = Depends(get_db_session)) -> SignalEnsemble:
    try:
        ensemble = SignalEnsembleService().create_ensemble(body)
    except ValueError as exc:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="ensemble_creation_failed",
            message=str(exc),
        ) from exc
    return _repo(db).create_signal_ensemble(ensemble)


@router.get("/ensembles/{ensemble_id}", response_model=SignalEnsemble)
def get_ensemble(ensemble_id: str, db: Session = Depends(get_db_session)) -> SignalEnsemble:
    ensemble = _repo(db).get_signal_ensemble(ensemble_id)
    if ensemble is None:
        raise not_found("signal_ensemble", ensemble_id)
    return ensemble


@router.post("/meta-labels", response_model=MetaLabel, status_code=status.HTTP_201_CREATED)
def create_meta_label(body: MetaLabelRequest, db: Session = Depends(get_db_session)) -> MetaLabel:
    if _repo(db).get_signal_ensemble(body.ensemble_id) is None:
        raise not_found("signal_ensemble", body.ensemble_id)
    try:
        label = SignalEnsembleService().create_meta_label(body)
    except ValueError as exc:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="meta_label_creation_failed",
            message=str(exc),
        ) from exc
    return _repo(db).create_meta_label(label)


@router.get("/meta-labels", response_model=CollectionResponse[MetaLabel])
def list_meta_labels(db: Session = Depends(get_db_session)) -> CollectionResponse[MetaLabel]:
    return collection_response(_repo(db).list_meta_labels())


@router.get("/meta-labels/{meta_label_id}", response_model=MetaLabel)
def get_meta_label(meta_label_id: str, db: Session = Depends(get_db_session)) -> MetaLabel:
    label = _repo(db).get_meta_label(meta_label_id)
    if label is None:
        raise not_found("meta_label", meta_label_id)
    return label
