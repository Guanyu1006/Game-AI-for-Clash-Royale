// Project-local V2/V3 APK signer using the Apache-2.0 Android apksig library.
// No Android SDK manager, device, account, or license-acceptance flag required.
import com.android.apksig.ApkSigner;
import com.android.apksig.ApkVerifier;
import java.io.File;
import java.io.FileInputStream;
import java.security.KeyStore;
import java.security.PrivateKey;
import java.security.cert.X509Certificate;
import java.util.ArrayList;
import java.util.List;

public class Phase2Sign {
    public static void main(String[] args) throws Exception {
        if (args.length != 5) throw new IllegalArgumentException("input output keystore alias password");
        char[] password = args[4].toCharArray();
        KeyStore store = KeyStore.getInstance("PKCS12");
        try (FileInputStream input = new FileInputStream(args[2])) { store.load(input, password); }
        PrivateKey key = (PrivateKey) store.getKey(args[3], password);
        List<X509Certificate> certificates = new ArrayList<>();
        for (java.security.cert.Certificate c : store.getCertificateChain(args[3])) certificates.add((X509Certificate)c);
        ApkSigner.SignerConfig signer = new ApkSigner.SignerConfig.Builder("phase2-local-research", key, certificates).build();
        new ApkSigner.Builder(List.of(signer))
            .setInputApk(new File(args[0])).setOutputApk(new File(args[1]))
            .setMinSdkVersion(24).setV1SigningEnabled(true).setV2SigningEnabled(true)
            .setV3SigningEnabled(true).setCreatedBy("Local phase2 reproducible research").build().sign();
        ApkVerifier.Result result = new ApkVerifier.Builder(new File(args[1]))
            .setMinCheckedPlatformVersion(24).build().verify();
        System.out.println("verified=" + result.isVerified());
        System.out.println("v1=" + result.isVerifiedUsingV1Scheme());
        System.out.println("v2=" + result.isVerifiedUsingV2Scheme());
        System.out.println("v3=" + result.isVerifiedUsingV3Scheme());
        System.out.println("errors=" + result.getErrors());
        if (!result.isVerified()) throw new IllegalStateException("APK verification failed");
    }
}
