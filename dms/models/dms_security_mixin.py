# Copyright 2020 Creu Blanca
# Copyright 2021 Tecnativa - Víctor Martínez
# Copyright 2024 Subteno - Timothée Vannier (https://www.subteno.com).
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).


from logging import getLogger

from odoo import api, fields, models
from odoo.osv.expression import (
    FALSE_DOMAIN,
    NEGATIVE_TERM_OPERATORS,
    OR,
    TRUE_DOMAIN,
)

_logger = getLogger(__name__)


class DmsSecurityMixin(models.AbstractModel):
    _name = "dms.security.mixin"
    _description = "DMS Security Mixin"

    # Submodels must define this field that points to the owner dms.directory
    _directory_field = "directory_id"

    res_model = fields.Char(
        string="Linked attachments model", index="btree", store=True
    )
    res_id = fields.Integer(
        string="Linked attachments record ID", index="btree", store=True
    )
    record_ref = fields.Reference(
        string="Record Referenced",
        compute="_compute_record_ref",
        selection=lambda self: self._get_ref_selection(),
    )
    permission_read = fields.Boolean(
        string="Read Access",
        default=True,
    )
    permission_create = fields.Boolean(
        string="Create Access",
        default=True,
    )
    permission_write = fields.Boolean(
        string="Write Access",
        default=True,
    )
    permission_unlink = fields.Boolean(
        string="Delete Access",
        default=True,
    )

    @api.model
    def _get_ref_selection(self):
        models = self.env["ir.model"].sudo().search([])
        return [(model.model, model.name) for model in models]

    @api.depends("res_model", "res_id")
    def _compute_record_ref(self):
        for record in self:
            record.record_ref = False
            if record.res_model and record.res_id:
                record.record_ref = f"{record.res_model},{record.res_id}"

    def _compute_permissions(self):
        """
        Get permissions for the current record.
        """

        # Update according to presence when applying ir.rule
        self.invalidate_recordset()
        if self.env.su:
            self.update(
                {
                    "permission_create": True,
                    "permission_read": True,
                    "permission_unlink": True,
                    "permission_write": True,
                }
            )
            return

        creatable = self._filter_access_rules("create")
        readable = self._filter_access_rules("read")
        unlinkable = self._filter_access_rules("unlink")
        writeable = self._filter_access_rules("write")
        for one in self:
            one.update(
                {
                    "permission_create": bool(one & creatable),
                    "permission_read": bool(one & readable),
                    "permission_unlink": bool(one & unlinkable),
                    "permission_write": bool(one & writeable),
                }
            )

    @api.model
    def _get_domain_by_inheritance(self, operation):
        """Get domain for inherited accessible records."""
        if self.env.su:
            return []
        inherited_access_field = "storage_id_inherit_access_from_parent_record"
        if self._name != "dms.directory":
            inherited_access_field = f"{self._directory_field}.{inherited_access_field}"
        inherited_access_domain = [
            ("storage_id_save_type", "=", "attachment"),
            (inherited_access_field, "=", True),
        ]
        domains = []
        # Get all used related records
        related_groups = self.sudo().read_group(
            domain=inherited_access_domain + [("res_model", "!=", False)],
            fields=["res_id:array_agg"],
            groupby=["res_model"],
        )
        for group in related_groups:
            try:
                model = self.env[group["res_model"]]
            except KeyError:
                # The model might not be registered.
                # This is normal if you are upgrading the database.
                # Otherwise, you probably have garbage DMS data.
                # These records will be accessible by DB users only.
                domains.append(
                    [
                        ("res_model", "=", group["res_model"]),
                        (True, "=", self.env.user.has_group("base.group_user")),
                    ]
                )
                continue
            # Check model access only once per batch
            if not model.check_access_rights(operation, raise_exception=False):
                continue
            domains.append([("res_model", "=", model._name), ("res_id", "=", False)])
            # Check record access in batch too
            res_ids = [i for i in group["res_id"] if i]  # Hack to remove None res_id
            # Apply exists to skip records that do not exist. (e.g. a res.partner
            # deleted by database).
            model_records = model.browse(res_ids).exists()
            related_ok = model_records._filter_access_rules_python(operation)
            if not related_ok:
                continue
            domains.append(
                [("res_model", "=", model._name), ("res_id", "in", related_ok.ids)]
            )
        result = inherited_access_domain + OR(domains)
        return result

    @api.model
    def _get_access_groups_query(self, operation):
        """Return the query to select access groups."""
        operation_check = {
            "create": "AND dag.perm_inclusive_create",
            "read": "",
            "unlink": "AND dag.perm_inclusive_unlink",
            "write": "AND dag.perm_inclusive_write",
        }[operation]
        select = f"""
            SELECT
                dir_group_rel.aid
            FROM
                dms_directory_complete_groups_rel AS dir_group_rel
                INNER JOIN dms_access_group AS dag
                    ON dir_group_rel.gid = dag.id
                INNER JOIN dms_access_group_users_rel AS users
                    ON users.gid = dag.id
            WHERE
                users.uid = %s {operation_check}
            """
        return select, (self.env.uid,)

    @api.model
    def _get_domain_by_access_groups(self, operation):
        """Get domain for records accessible applying DMS access groups."""
        result = [
            (
                "%s.storage_id_inherit_access_from_parent_record"
                % self._directory_field,
                "=",
                False,
            ),
            (
                self._directory_field,
                "inselect",
                self._get_access_groups_query(operation),
            ),
        ]
        return result

    @api.model
    def _get_permission_domain(self, operator, value, operation):
        """Abstract logic for searching computed permission fields."""
        _self = self
        # HACK ir.rule domain is always computed with sudo, so if this check is
        # true, we can assume safely that you're checking permissions
        if self.env.su and value == self.env.uid:
            _self = self.sudo(False)
            value = bool(value)
        # Tricky one, to know if you want to search
        # positive or negative access
        positive = (operator not in NEGATIVE_TERM_OPERATORS) == bool(value)
        if _self.env.su:
            # You're SUPERUSER_ID
            return TRUE_DOMAIN if positive else FALSE_DOMAIN

        result = OR(
            [
                _self._get_domain_by_access_groups(operation),
                _self._get_domain_by_inheritance(operation),
            ]
        )

        if not positive:
            result.insert(0, "!")
        return result

    @api.model
    def _search_permission_create(self, operator, value):
        return self._get_permission_domain(operator, value, "create")

    @api.model
    def _search_permission_read(self, operator, value):
        return self._get_permission_domain(operator, value, "read")

    @api.model
    def _search_permission_unlink(self, operator, value):
        return self._get_permission_domain(operator, value, "unlink")

    @api.model
    def _search_permission_write(self, operator, value):
        return self._get_permission_domain(operator, value, "write")

    def _filter_access_rules_python(self, operation):
        # Only kept to not break inheritance; see next comment
        result = super()._filter_access_rules_python(operation)
        # HACK Always fall back to applying rules by SQL.
        # Upstream `_filter_access_rules_python()` doesn't use computed fields
        # search methods. Thus, it will take the `[('permission_{operation}',
        # '=', user.id)]` rule literally. Obviously that will always fail
        # because `self[f"permission_{operation}"]` will always be a `bool`,
        # while `user.id` will always be an `int`.
        result |= self._filter_access_rules(operation)
        return result

    @api.model_create_multi
    def create(self, vals_list):
        # Create as sudo to avoid testing creation permissions before DMS security
        # groups are attached (otherwise nobody would be able to create)
        res = super(DmsSecurityMixin, self.sudo()).create(vals_list)
        # Need to flush now, so all groups are stored in DB and the SELECT used
        # to check access works
        res.flush_recordset()
        # Go back to the original sudo state and check we really had creation permission
        res = res.sudo(self.env.su)
        res.check_access_rights("create")
        res.check_access_rule("create")
        return res
