# 通过 SAML 将 Microsoft Entra ID 集成到 MediaCMS

本文介绍使用 SAML 将 Microsoft Entra ID（原 Azure AD）配置为 MediaCMS 身份提供商。

## 前置条件

- 同时拥有 MediaCMS 和 Microsoft Entra/Azure Portal 管理权限。
- MediaCMS 通过 HTTPS 访问，并配置有效 SSL 证书。
- MediaCMS 已启用 `django-allauth` SAML 支持。
- 已准备 MediaCMS 专用域名或子域名，例如 `https://<MyMediaCMS.MyDomain.com`。

## 1. 注册 Entra 企业应用

登录 [Azure Portal](https://portal.azure.com)，进入 **Enterprise Applications**，选择 **New Application → Create your own application**，创建一个非 Gallery 应用，例如 `MediaCMS`。在应用中进入 **Single sign-on → SAML**。

在 Basic SAML Configuration 中填写：

| 字段 | 值 |
| --- | --- |
| Identifier（Entity ID） | `https://<domain>/saml/metadata/` |
| Reply URL（ACS URL） | `https://<domain>/accounts/saml/<client-id>/acs/` |
| Sign-on URL | `https://<domain>/accounts/saml/<client-id>/login/` |
| Relay State | `https://<domain>/` |
| Logout URL | `https://<domain>/accounts/saml/<client-id>/sls/` |

记录 Entra Identifier、登录/退出 URL、证书和下载的 Federation Metadata XML。

## 2. 在 MediaCMS 中配置

在 **Identity Providers → Login Options** 添加登录选项，填写清晰的标题（如 `EntraID-SSO`）和与 Entra 相同的 Sign-on URL，启用该选项。

然后进入 **Identity Providers → ID Providers** 添加提供商：协议为 `saml`，Provider ID 使用 Entra Identifier，配置名称和 Client ID 与 Entra 保持一致，并关联需要显示该登录方式的站点。填写 SSO URL、SLO URL、SP Metadata URL、IdP ID 和 Entra 签名证书。

身份属性映射通常为：

| MediaCMS 字段 | Entra SAML Claim |
| --- | --- |
| Uid | `http://schemas.microsoft.com/identity/claims/objectidentifier` |
| Name | `http://schemas.microsoft.com/identity/claims/displayname` |
| Email | `http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress` |
| First name | `http://schemas.xmlsoap.org/ws/2005/05/identity/claims/givenname` |
| Last name | `http://schemas.xmlsoap.org/ws/2005/05/identity/claims/surname` |

## 3. 分配用户并测试

在 Entra 企业应用的 **Users and Groups** 中分配允许登录 MediaCMS 的用户或组。之后从 MediaCMS 登录页选择 Entra 登录选项，验证登录、用户创建/匹配、角色映射和退出流程。

## 故障排查

重点检查 ACS URL、Entity ID、Client ID、证书、时间同步、HTTPS 反向代理头和用户/组 Claim。若出现无限重定向，确认 MediaCMS 中 Login Option URL 与 Entra Sign-on URL 完全一致，并检查代理是否正确传递 HTTPS 协议。

调试 SAML 响应时，可复制 Base64 内容并使用 [CyberChef](https://gchq.github.io/CyberChef/) 的 From Base64 和 XML Beautify 工具查看 XML；注意不要在公开渠道暴露真实响应或证书私密信息。

> 英文原文：[saml_entraid_setup.md](saml_entraid_setup.md)
