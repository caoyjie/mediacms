from .template_loader import load_template


CERTIFICATE = "infra/aws/mediacms-certificate.yaml"


def test_certificate_stack_contains_only_dns_validated_acm_certificate():
    template = load_template(CERTIFICATE)
    resources = template["Resources"]
    assert set(resources) == {"MediaCertificate"}
    certificate = resources["MediaCertificate"]
    assert certificate["Type"] == "AWS::CertificateManager::Certificate"
    props = certificate["Properties"]
    assert props["ValidationMethod"] == "DNS"
    assert props["KeyAlgorithm"] == "RSA_2048"
    assert props["CertificateTransparencyLoggingPreference"] == "ENABLED"
    assert not any(
        resource["Type"].startswith("AWS::Route53::")
        for resource in resources.values()
    )


def test_certificate_stack_is_environment_scoped_and_outputs_only_arn():
    template = load_template(CERTIFICATE)
    assert template["Parameters"]["Environment"]["AllowedValues"] == ["dev", "prod"]
    certificate = template["Resources"]["MediaCertificate"]
    assert certificate["Properties"]["DomainName"] == {"Ref": "MediaDomainName"}
    assert certificate["Properties"]["Tags"] == [
        {"Key": "Project", "Value": "mediacms"},
        {"Key": "Environment", "Value": {"Ref": "Environment"}},
    ]
    assert set(template["Outputs"]) == {"CertificateArn"}
    assert template["Outputs"]["CertificateArn"]["Value"] == {
        "Ref": "MediaCertificate"
    }
