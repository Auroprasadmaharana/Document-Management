# Copyright 2017-2019 MuK IT GmbH
# Copyright 2020 RGB Consulting
# Copyright 2024 Timothée Vannier - Subteno (https://www.subteno.com).
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class DmsAccessGroups(models.Model):
    _name = "dms.access.group"
    _description = "Record Access Groups"
    _parent_store = True
    _parent_name = "parent_group_id"

    name = fields.Char(string="Group Name", required=True, translate=True)
    parent_path = fields.Char(index="btree", unaccent=False)

    # Permissions written directly on this group
    perm_create = fields.Boolean(string="Create Access")
    perm_write = fields.Boolean(string="Write Access")
    perm_unlink = fields.Boolean(string="Delete Access")
    perm_read = fields.Boolean(string="Read Access")  # ✅ New Field

    # Permissions computed including parent group
    perm_inclusive_create = fields.Boolean(
        string="Inherited Create Access",
        compute="_compute_inclusive_permissions",
        store=True,
        recursive=True,
    )
    perm_inclusive_write = fields.Boolean(
        string="Inherited Write Access",
        compute="_compute_inclusive_permissions",
        store=True,
        recursive=True,
    )
    perm_inclusive_unlink = fields.Boolean(
        string="Inherited Unlink Access",
        compute="_compute_inclusive_permissions",
        store=True,
        recursive=True,
    )
    perm_inclusive_read = fields.Boolean(  # ✅ New Field
        string="Inherited Read Access",
        compute="_compute_inclusive_permissions",
        store=True,
        recursive=True,
    )

    directory_ids = fields.Many2many(
        comodel_name="dms.directory",
        relation="dms_directory_groups_rel",
        string="Directories",
        column1="gid",
        column2="aid",
        auto_join=True,
        readonly=True,
    )
    complete_directory_ids = fields.Many2many(
        comodel_name="dms.directory",
        relation="dms_directory_complete_groups_rel",
        column1="gid",
        column2="aid",
        string="Complete directories",
        auto_join=True,
        readonly=True,
    )
    count_users = fields.Integer(compute="_compute_users", store=True)
    count_directories = fields.Integer(compute="_compute_count_directories")
    parent_group_id = fields.Many2one(
        comodel_name="dms.access.group",
        string="Parent Group",
        ondelete="cascade",
        index="btree",
    )
    child_group_ids = fields.One2many(
        comodel_name="dms.access.group",
        inverse_name="parent_group_id",
        string="Child Groups",
    )
    group_ids = fields.Many2many(
        comodel_name="res.groups",
        relation="dms_access_group_groups_rel",
        column1="gid",
        column2="rid",
        string="Groups",
    )
    explicit_user_ids = fields.Many2many(
        comodel_name="res.users",
        relation="dms_access_group_explicit_users_rel",
        column1="gid",
        column2="uid",
        string="Explicit Users",
    )
    users = fields.Many2many(
        comodel_name="res.users",
        relation="dms_access_group_users_rel",
        column1="gid",
        column2="uid",
        string="Group Users",
        compute="_compute_users",
        auto_join=True,
        store=True,
        recursive=True,
    )

    @api.depends("directory_ids")
    def _compute_count_directories(self):
        for record in self:
            record.count_directories = len(record.directory_ids)

    _sql_constraints = [
        ("name_uniq", "unique (name)", "The name of the group must be unique!")
    ]

    @api.depends(
        "parent_group_id.perm_inclusive_create",
        "parent_group_id.perm_inclusive_unlink",
        "parent_group_id.perm_inclusive_write",
        "parent_group_id.perm_inclusive_read",  # ✅ New Dependency
        "parent_path",
        "perm_create",
        "perm_unlink",
        "perm_write",
        "perm_read",  # ✅ New Dependency
    )
    def _compute_inclusive_permissions(self):
        """Provide full permissions inheriting from parent recursively."""
        for one in self:
            one.update(
                {
                    "perm_inclusive_%s" % perm: (
                        one["perm_%s" % perm]
                        or one.parent_group_id["perm_inclusive_%s" % perm]
                    )
                    for perm in ("create", "unlink", "write", "read")  # ✅ Include 'read'
                }
            )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if res.get("explicit_user_ids"):
            res["explicit_user_ids"] = res["explicit_user_ids"] + [self.env.uid]
        else:
            res["explicit_user_ids"] = [(6, 0, [self.env.uid])]
        return res

    @api.depends(
        "parent_group_id",
        "parent_group_id.users",
        "group_ids",
        "group_ids.users",
        "explicit_user_ids",
    )
    def _compute_users(self):
        for record in self:
            users = (
                record.group_ids.users
                | record.explicit_user_ids
                | record.parent_group_id.users
            )
            record.update({"users": users, "count_users": len(users)})

    def copy(self, default=None):
        default = dict(default or {})
        default["name"] = _("%s (copy)") % self.name
        return super().copy(default=default)

    @api.constrains("parent_path")
    def _check_parent_recursiveness(self):
        """
        Forbid recursive relationships.
        """
        for one in self.filtered("parent_group_id"):
            if str(one.id) in one.parent_path.split("/"):
                raise ValidationError(
                    _("Parent group '%(parent)s' is child of '%(current)s'.")
                    % {
                        "parent": one.parent_group_id.display_name,
                        "current": one.display_name,
                    }
                )
