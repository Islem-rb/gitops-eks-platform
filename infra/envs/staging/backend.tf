terraform {
  backend "s3" {
    bucket       = "tfstate-455939971919-eu-central-1"
    key          = "staging/terraform.tfstate"
    region       = "eu-central-1"
    encrypt      = true
    use_lockfile = true
  }
}
