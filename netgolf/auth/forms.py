"""
Form per register / login / profilo / credenziali FIG.

I messaggi di validazione passano per lazy_gettext, così vengono tradotti
al momento del render in base al locale della richiesta.
"""

from flask_babel import lazy_gettext as _l
from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField, SubmitField
from wtforms.validators import (
    DataRequired,
    Email,
    EqualTo,
    Length,
    Optional,
    Regexp,
)


class RegisterForm(FlaskForm):
    email = StringField(
        _l("Email"),
        validators=[DataRequired(), Email(), Length(max=254)],
    )
    password = PasswordField(
        _l("Password"),
        validators=[
            DataRequired(),
            Length(min=10, message=_l("La password deve contenere almeno 10 caratteri.")),
        ],
    )
    password_confirm = PasswordField(
        _l("Conferma password"),
        validators=[
            DataRequired(),
            EqualTo("password", message=_l("Le password non coincidono.")),
        ],
    )
    submit = SubmitField(_l("Crea account"))


class LoginForm(FlaskForm):
    email = StringField(_l("Email"), validators=[DataRequired(), Email()])
    password = PasswordField(_l("Password"), validators=[DataRequired()])
    remember = BooleanField(_l("Ricordami"))
    submit = SubmitField(_l("Accedi"))


class FigCredentialsForm(FlaskForm):
    """
    Form per salvare/aggiornare tessera e password dell'area riservata FIG.
    Entrambi i campi sono opzionali: se l'utente svuota tessera + password
    e salva, le credenziali vengono cancellate.
    """

    tessera = StringField(
        _l("Numero tessera FIG"),
        validators=[
            Optional(),
            Regexp(
                r"^\d{4,8}$",
                message=_l("Il numero tessera deve contenere da 4 a 8 cifre."),
            ),
        ],
    )
    password_fig = PasswordField(
        _l("Password area riservata FIG"),
        validators=[Optional(), Length(max=200)],
    )
    remove = BooleanField(_l("Rimuovi credenziali FIG salvate"))
    submit = SubmitField(_l("Salva"))
