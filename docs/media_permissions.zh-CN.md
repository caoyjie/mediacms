# MediaCMS 媒体权限

MediaCMS 提供三层媒体访问控制：公开/私有/不公开列出等基础状态、面向个人用户的直接授权，以及按分类和用户组工作的 RBAC。

## 媒体状态

- **公开（Public）**：所有人可见。
- **私有（Private）**：仅所有者和拥有显式权限的用户可见。
- **不公开列出（Unlisted）**：不出现在公开列表中，但知道链接即可访问。

## 用户角色

普通用户可管理自己的媒体；高级用户拥有额外配置能力；MediaCMS Editor 可编辑和审核平台内容；Manager 拥有更完整的管理能力；Admin 拥有系统级权限。

## 直接媒体权限

`MediaPermission` 可以为指定用户授予 Viewer（查看）、Editor（查看并编辑元数据）或 Owner（完整控制，包括删除）权限。

## RBAC

启用 `USE_RBAC` 后，分类可以关联 RBAC 用户组，用户通过组成员关系继承分类内媒体权限：Member 可查看，Contributor 可查看和编辑，Manager 拥有完整管理能力。

## 权限判断顺序

访问媒体时，系统依次检查：媒体是否公开、用户是否为所有者、是否存在 `MediaPermission` 直接授权，以及启用 RBAC 时用户是否通过分类组成员获得权限；全部不满足时拒绝访问。

常用检查方法位于 `users/models.py`：`has_member_access_to_media`、`has_contributor_access_to_media` 和 `has_owner_access_to_media`。

## 最佳实践

建议新上传媒体默认设为私有；使用分类组织媒体；团队协作使用 RBAC；临时或例外共享使用直接权限。

> 英文原文：[media_permissions.md](media_permissions.md)
