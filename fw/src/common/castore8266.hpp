// CA certificates for BearSSL (ESP8266), from flash

#ifndef CASTORE8266_HPP
#define CASTORE8266_HPP

#if defined(ESP8266)

#include <CertStoreBearSSL.h>

// Certificate store with the CA certificates of the firmware (made by tools/gen_ca_bundle.py)
class CaStore : public BearSSL::CertStoreBase
{
  public:
    /**
     * @brief Install into the X.509 validator (WiFiClientSecure::setCertStore())
     */
    void installCertStore(br_x509_minimal_context* ctx) override;

    /**
     * @brief Check the store: hash of the first and last certificate as BearSSL makes it
     *
     * Prints the result.
     *
     * @returns true if OK
     */
    bool check();

  private:
    BearSSL::X509List* x509 = nullptr;  // Trust anchor in use

    static const br_x509_trust_anchor* findTa(void* ctx, void* hashedDn, size_t len);
    static void freeTa(void* ctx, const br_x509_trust_anchor* ta);
};

#endif // ESP8266

#endif // CASTORE8266_HPP
